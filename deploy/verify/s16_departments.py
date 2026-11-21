"""组织架构与分派范围（020）的真实栈验收。

四组场景：

- **A 部门树**：admin 建 root → child，path/depth 由后端拼；列表按 path 先序。
- **B 权限**：非 admin 建部门 403。
- **C 分派门禁**：受限部门（scope=self）的人转交需求给范围外的人 → 403；
  转给自己 → 202。
- **D 默认不收紧**：没有部门的人可以转交给任何人（升级后行为零变化）。

用法：
    python deploy/verify/s16_departments.py --base http://localhost:8080 \
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


class DepartmentVerifier:
    def __init__(
        self, base: str, invite: str, admin_email: str, admin_password: str
    ) -> None:
        self.api = f"{base.rstrip('/')}/api/v1"
        self.invite = invite
        self.admin_email = admin_email
        self.admin_password = admin_password
        self.run_id = uuid.uuid4().hex[:6]
        self.http = httpx.Client(timeout=30.0)
        self.results: list[Result] = []
        self.tokens: dict[str, str] = {}
        self.ids: dict[str, str] = {}

    def _record(self, name: str, ok: bool, detail: str, started: float) -> None:
        self.results.append(Result(name, ok, detail, time.monotonic() - started))

    def _h(self, who: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.tokens[who]}"}

    def _post(self, who: str, path: str, json: dict | None = None) -> httpx.Response:
        return self.http.post(f"{self.api}{path}", json=json, headers=self._h(who))

    def _get(self, who: str, path: str, **params) -> httpx.Response:
        return self.http.get(f"{self.api}{path}", headers=self._h(who), params=params)

    def _put(self, who: str, path: str, json: dict | None = None) -> httpx.Response:
        return self.http.put(f"{self.api}{path}", json=json, headers=self._h(who))

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
        self.ids["admin"] = login.json()["user"]["id"]

        for who in ("actor", "outsider", "free"):
            resp = self.http.post(
                f"{self.api}/auth/register",
                json={
                    "email": f"s16-{who}-{self.run_id}@test.com",
                    "password": PASSWORD,
                    "invite_code": self.invite,
                },
            )
            assert resp.status_code == 201, f"注册 {who} 失败：{resp.text[:200]}"
            body = resp.json()
            self.tokens[who] = body["tokens"]["access"]
            self.ids[who] = body["user"]["id"]

    def run(self) -> int:
        self.setup()

        # A 部门树
        started = time.monotonic()
        root = self._post(
            "admin",
            "/departments",
            {"name": f"验收根-{self.run_id}", "assign_scope": "self"},
        )
        child = self._post(
            "admin",
            "/departments",
            {"name": f"验收子-{self.run_id}", "parent_id": root.json()["id"]},
        )
        listed = self._get("admin", "/departments").json()["items"]
        root_row = next(d for d in listed if d["id"] == root.json()["id"])
        child_row = next(d for d in listed if d["id"] == child.json()["id"])
        ok = (
            root.status_code == 201
            and child.status_code == 201
            and root_row["path"] == f"/{root_row['id']}/"
            and child_row["path"] == f"{root_row['path']}{child_row['id']}/"
            and (root_row["depth"], child_row["depth"]) == (0, 1)
        )
        self._record(
            "A1 部门树（path/depth 由后端拼）",
            ok,
            f"root={root_row['path']} child={child_row['path']}",
            started,
        )

        # B 权限
        started = time.monotonic()
        denied = self._post("actor", "/departments", {"name": f"越权-{self.run_id}"})
        self._record(
            "B1 非 admin 建部门 403",
            denied.status_code == 403,
            str(denied.status_code),
            started,
        )

        # C 分派门禁：actor 放进 scope=self 的根部门，outsider 放另一部门
        started = time.monotonic()
        other = self._post(
            "admin", "/departments", {"name": f"验收外部-{self.run_id}"}
        ).json()
        self._put("admin", f"/departments/users/{self.ids['actor']}/department", {"department_id": root_row["id"]})
        self._put(
            "admin",
            f"/departments/users/{self.ids['outsider']}/department",
            {"department_id": other["id"]},
        )
        req = self._post(
            "actor", "/requests", {"title": f"验收组织门禁-{self.run_id}"}
        ).json()
        blocked = self._post(
            "actor",
            f"/requests/{req['id']}/assign",
            {"assignee_type": "user", "assignee_id": self.ids["outsider"]},
        )
        to_self = self._post(
            "actor",
            f"/requests/{req['id']}/assign",
            {"assignee_type": "user", "assignee_id": self.ids["actor"]},
        )
        self._record(
            "C1 受限部门派给范围外 403 / 派给自己 202",
            blocked.status_code == 403 and to_self.status_code == 202,
            f"blocked={blocked.status_code} to_self={to_self.status_code}",
            started,
        )

        # D 默认不收紧：free 没部门，可以派给 outsider
        started = time.monotonic()
        req2 = self._post(
            "free", "/requests", {"title": f"验收无部门-{self.run_id}"}
        ).json()
        allowed = self._post(
            "free",
            f"/requests/{req2['id']}/assign",
            {"assignee_type": "user", "assignee_id": self.ids["outsider"]},
        )
        self._record(
            "D1 未归属部门的人不受范围限制（升级零变化）",
            allowed.status_code == 202,
            str(allowed.status_code),
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
    parser = argparse.ArgumentParser(description="020 组织架构真实栈验收")
    parser.add_argument("--base", default="http://localhost:8080")
    parser.add_argument("--invite", required=True)
    parser.add_argument("--admin-email", required=True)
    parser.add_argument("--admin-password", required=True)
    args = parser.parse_args()
    sys.exit(
        DepartmentVerifier(
            args.base, args.invite, args.admin_email, args.admin_password
        ).run()
    )


if __name__ == "__main__":
    main()
