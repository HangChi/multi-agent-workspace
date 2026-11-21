"""邀请码申请（027）的真实栈验收。

四组场景：

- **A 提交**：member 提申请 → 201 pending。
- **B 批准发码**：admin 批准 → approved，issued_codes 里 2 个码，且能用其中一个注册
  （证明码是真的）。
- **C 配额门禁**：部门 quota=1 的申请 count=3 → 批准 409，申请仍 pending（不出现
  「批了但没码」）。
- **D 权限**：member 批准 → 403。

用法：
    python deploy/verify/s22_invite_requests.py --base http://localhost:8080 \
        --invite <INVITE> --admin-email <ADMIN> --admin-password <PWD>
"""

from __future__ import annotations

import argparse
import sys
import time
import uuid
from dataclasses import dataclass

import httpx

PASSWORD = "TestPass123!"


@dataclass
class Result:
    name: str
    ok: bool
    detail: str = ""
    elapsed_s: float = 0.0

    def line(self) -> str:
        mark = "✅" if self.ok else "❌"
        cost = f"（{self.elapsed_s:.1f}s）" if self.elapsed_s else ""
        return f"{mark} {self.name} — {self.detail}{cost}"


class RequestVerifier:
    def __init__(self, base: str, invite: str, admin_email: str, admin_password: str):
        self.api = f"{base.rstrip('/')}/api/v1"
        self.invite = invite
        self.admin_email = admin_email
        self.admin_password = admin_password
        self.run_id = uuid.uuid4().hex[:6]
        self.http = httpx.Client(timeout=30.0)
        self.results: list[Result] = []
        self.tokens: dict[str, str] = {}

    def _h(self, who: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.tokens[who]}"}

    def _post(self, who: str, path: str, json: dict | None = None) -> httpx.Response:
        return self.http.post(f"{self.api}{path}", json=json, headers=self._h(who))

    def _patch(self, who: str, path: str, json: dict) -> httpx.Response:
        return self.http.patch(f"{self.api}{path}", json=json, headers=self._h(who))

    def _record(self, name: str, ok: bool, detail: str, started: float) -> None:
        self.results.append(Result(name, ok, detail, time.monotonic() - started))

    def setup(self) -> None:
        login = self.http.post(
            f"{self.api}/auth/login",
            json={"email": self.admin_email, "password": self.admin_password},
        )
        if login.status_code != 200:
            raise SystemExit("admin 登录失败：需要 --admin-email/--admin-password")
        self.tokens["admin"] = login.json()["tokens"]["access"]

        reg = self.http.post(
            f"{self.api}/auth/register",
            json={
                "email": f"s22-member-{self.run_id}@test.com",
                "password": PASSWORD,
                "invite_code": self.invite,
            },
        )
        assert reg.status_code == 201, reg.text[:200]
        self.tokens["member"] = reg.json()["tokens"]["access"]

    def run(self) -> int:
        self.setup()

        # A 提交
        started = time.monotonic()
        created = self._post(
            "member",
            "/invite-requests",
            {"role": "member", "count": 2, "reason": "s22 验收：新同事入职"},
        )
        body = created.json() if created.status_code == 201 else {}
        self._record(
            "A1 member 提申请 → pending",
            created.status_code == 201 and body.get("status") == "pending",
            f"{created.status_code} {body.get('status')}",
            started,
        )
        request_id = body.get("id")

        # B 批准发码 + 码可用
        started = time.monotonic()
        approved = self._post("admin", f"/invite-requests/{request_id}/approve")
        approved_body = approved.json() if approved.status_code == 200 else {}
        codes = (approved_body.get("issued_codes") or "").splitlines()
        usable = False
        if len(codes) == 2:
            reg = self.http.post(
                f"{self.api}/auth/register",
                json={
                    "email": f"s22-hired-{self.run_id}@test.com",
                    "password": PASSWORD,
                    "invite_code": codes[0],
                },
            )
            usable = reg.status_code == 201
        self._record(
            "B1 批准即发码，且码真的能注册",
            approved.status_code == 200
            and approved_body.get("status") == "approved"
            and len(codes) == 2
            and usable,
            f"codes={len(codes)} usable={usable}",
            started,
        )

        # C 配额门禁
        started = time.monotonic()
        dept = self._post(
            "admin", "/departments", {"name": f"s22-dept-{self.run_id}"}
        ).json()
        self._patch("admin", f"/departments/{dept['id']}", {"invite_quota": 1})
        tight = self._post(
            "member",
            "/invite-requests",
            {
                "role": "member",
                "count": 3,
                "reason": "s22 配额测试",
                "department_id": dept["id"],
            },
        ).json()
        denied = self._post("admin", f"/invite-requests/{tight['id']}/approve")
        still = self._post("admin", f"/invite-requests/{tight['id']}/reject", {"reason": "cleanup"})
        self._record(
            "C1 部门配额不足 → 批准 409，申请仍 pending",
            denied.status_code == 409 and still.status_code == 200,
            f"approve={denied.status_code}",
            started,
        )

        # D 权限
        started = time.monotonic()
        another = self._post(
            "member",
            "/invite-requests",
            {"role": "member", "count": 1, "reason": "s22 权限测试"},
        ).json()
        blocked = self._post("member", f"/invite-requests/{another['id']}/approve")
        self._record(
            "D1 member 批准申请 403",
            blocked.status_code == 403,
            str(blocked.status_code),
            started,
        )

        self.http.close()
        print()
        for r in self.results:
            print(r.line())
        failed = [r for r in self.results if not r.ok]
        print(
            f"\n合计 {len(self.results)} 场景：{len(self.results) - len(failed)} 通过 / {len(failed)} 失败"
        )
        return 1 if failed else 0


def main() -> None:
    parser = argparse.ArgumentParser(description="027 邀请码申请真实栈验收")
    parser.add_argument("--base", default="http://localhost:8080")
    parser.add_argument("--invite", required=True)
    parser.add_argument("--admin-email", required=True)
    parser.add_argument("--admin-password", required=True)
    args = parser.parse_args()
    sys.exit(
        RequestVerifier(
            args.base, args.invite, args.admin_email, args.admin_password
        ).run()
    )


if __name__ == "__main__":
    main()
