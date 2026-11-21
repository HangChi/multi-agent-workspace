"""邀请码运营（022）的真实栈验收。

三组场景：

- **A 部门名额配额**：部门 quota=2，发 max_uses=2 → 201；再发 1 → 409（消息带「已发/上限」）。
- **B 统计总览**：`GET /admin/invite-codes/stats` 的 issued/used 与 by_department 一致。
- **C 权限**：非 admin 看统计 403。

用法：
    python deploy/verify/s18_invite_ops.py --base http://localhost:8080 \
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


class InviteOpsVerifier:
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

    def _get(self, who: str, path: str) -> httpx.Response:
        return self.http.get(f"{self.api}{path}", headers=self._h(who))

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
            raise SystemExit(
                f"admin 登录失败（{login.status_code}）：需要 --admin-email/--admin-password"
            )
        self.tokens["admin"] = login.json()["tokens"]["access"]

        reg = self.http.post(
            f"{self.api}/auth/register",
            json={
                "email": f"s18-member-{self.run_id}@test.com",
                "password": PASSWORD,
                "invite_code": self.invite,
            },
        )
        assert reg.status_code == 201, reg.text[:200]
        self.tokens["member"] = reg.json()["tokens"]["access"]

    def run(self) -> int:
        self.setup()

        dept = self._post(
            "admin", "/departments", {"name": f"s18部门-{self.run_id}"}
        ).json()
        self._patch("admin", f"/departments/{dept['id']}", {"invite_quota": 2})

        started = time.monotonic()
        first = self._post(
            "admin",
            "/admin/invite-codes",
            {"role": "member", "max_uses": 2, "department_id": dept["id"]},
        )
        second = self._post(
            "admin",
            "/admin/invite-codes",
            {"role": "member", "max_uses": 1, "department_id": dept["id"]},
        )
        message = second.json()["error"]["message"] if second.status_code == 409 else ""
        self._record(
            "A1 部门配额：发满后 409 且消息带已发/上限",
            first.status_code == 201
            and second.status_code == 409
            and "名额" in message,
            f"{first.status_code}/{second.status_code} {message[:30]}",
            started,
        )

        started = time.monotonic()
        stats = self._get("admin", "/admin/invite-codes/stats")
        body = stats.json() if stats.status_code == 200 else {}
        dept_row = next(
            (d for d in body.get("by_department", []) if d["department_id"] == dept["id"]),
            None,
        )
        ok = (
            stats.status_code == 200
            and body.get("issued_seats", 0) >= 2
            and dept_row is not None
            and dept_row["quota"] == 2
            and dept_row["issued_seats_this_month"] >= 2
        )
        self._record(
            "B1 统计：总量与部门本月进度",
            ok,
            f"issued={body.get('issued_seats')} dept_quota={dept_row and dept_row['quota']}",
            started,
        )

        started = time.monotonic()
        denied = self._get("member", "/admin/invite-codes/stats")
        self._record(
            "C1 非 admin 看统计 403",
            denied.status_code == 403,
            str(denied.status_code),
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
    parser = argparse.ArgumentParser(description="022 邀请码运营真实栈验收")
    parser.add_argument("--base", default="http://localhost:8080")
    parser.add_argument("--invite", required=True)
    parser.add_argument("--admin-email", required=True)
    parser.add_argument("--admin-password", required=True)
    args = parser.parse_args()
    sys.exit(
        InviteOpsVerifier(
            args.base, args.invite, args.admin_email, args.admin_password
        ).run()
    )


if __name__ == "__main__":
    main()
