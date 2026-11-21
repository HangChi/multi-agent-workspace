"""项目成员角色（019）的真实栈验收。

四组场景：

- **A 角色落库**：加 viewer 成员后列表返回 role=viewer；owner 行的 role 是 owner。
- **B 只读能看**：viewer 读项目详情、读需求详情、按项目筛需求，全部 200。
- **C 只读改不了**：viewer 改需求 403、分配执行 403，且需求内容没被改动。
- **D 编辑能改**：默认 editor 的成员 PATCH 需求 200（010 的既有行为没被弄坏）。

用法：
    python deploy/verify/s15_project_roles.py --base http://localhost:8080 --invite <INVITE>
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


class RoleVerifier:
    def __init__(self, base: str, invite: str) -> None:
        self.api = f"{base.rstrip('/')}/api/v1"
        self.invite = invite
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

    def _patch(self, who: str, path: str, json: dict) -> httpx.Response:
        return self.http.patch(f"{self.api}{path}", json=json, headers=self._h(who))

    def _get(self, who: str, path: str, **params) -> httpx.Response:
        return self.http.get(f"{self.api}{path}", headers=self._h(who), params=params)

    def setup(self) -> None:
        for who in ("owner", "viewer", "editor"):
            resp = self.http.post(
                f"{self.api}/auth/register",
                json={
                    "email": f"s15-{who}-{self.run_id}@test.com",
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

        project = self._post(
            "owner", "/projects", {"name": f"s15角色-{self.run_id}"}
        ).json()
        request = self._post(
            "owner",
            "/requests",
            {"title": f"s15需求-{self.run_id}", "project_id": project["id"]},
        ).json()

        # A 角色落库
        started = time.monotonic()
        r1 = self._post(
            "owner",
            f"/projects/{project['id']}/members",
            {"user_id": self.ids["viewer"], "role": "viewer"},
        )
        r2 = self._post(
            "owner",
            f"/projects/{project['id']}/members",
            {"user_id": self.ids["editor"]},
        )
        listed = self._get("owner", f"/projects/{project['id']}/members").json()["items"]
        roles = {m["user_id"]: m["role"] for m in listed}
        ok = (
            r1.status_code == 201
            and r2.status_code == 201
            and roles.get(self.ids["viewer"]) == "viewer"
            and roles.get(self.ids["editor"]) == "editor"
            and roles.get(self.ids["owner"]) == "owner"
        )
        self._record("A1 角色落库（viewer/editor/owner）", ok, str(roles), started)

        # B 只读能看
        started = time.monotonic()
        checks = {
            "项目详情": self._get("viewer", f"/projects/{project['id']}").status_code,
            "需求详情": self._get("viewer", f"/requests/{request['id']}").status_code,
            "按项目筛": self._get(
                "viewer", "/requests", project_id=project["id"]
            ).status_code,
        }
        self._record(
            "B1 只读成员能读（项目/需求/列表）",
            all(code == 200 for code in checks.values()),
            str(checks),
            started,
        )

        # C 只读改不了
        started = time.monotonic()
        patch = self._patch(
            "viewer", f"/requests/{request['id']}", {"title": "viewer 改的标题"}
        )
        agent = self._post(
            "owner",
            "/agents",
            {
                "name": f"s15-agent-{self.run_id}",
                "system_prompt": "x",
                "model": "Deepseek-V4.1-Flash",
                "max_steps": 2,
                "cost_limit_cents": 1000,
                "tools": [],
            },
        ).json()
        assign = self._post(
            "viewer",
            f"/requests/{request['id']}/assign",
            {"assignee_type": "agent", "assignee_id": agent["id"]},
        )
        after = self._get("owner", f"/requests/{request['id']}").json()
        ok = (
            patch.status_code == 403
            and assign.status_code == 403
            and after["title"] == request["title"]
        )
        self._record(
            "C1 只读成员改需求/分配均 403 且状态未变",
            ok,
            f"patch={patch.status_code} assign={assign.status_code}",
            started,
        )

        # D 编辑能改
        started = time.monotonic()
        resp = self._patch(
            "editor", f"/requests/{request['id']}", {"title": "editor 改的标题"}
        )
        self._record(
            "D1 editor 能改需求（010 行为未破）",
            resp.status_code == 200 and resp.json()["title"] == "editor 改的标题",
            f"status={resp.status_code}",
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
    parser = argparse.ArgumentParser(description="019 项目成员角色真实栈验收")
    parser.add_argument("--base", default="http://localhost:8080")
    parser.add_argument("--invite", required=True)
    args = parser.parse_args()
    sys.exit(RoleVerifier(args.base, args.invite).run())


if __name__ == "__main__":
    main()
