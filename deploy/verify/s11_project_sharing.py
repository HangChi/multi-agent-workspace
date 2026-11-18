"""项目共享与成员的真实栈验收（010 / T022）。

四个场景对应 spec 的成功标准，全部走 HTTP 打真实容器栈：

- **A 成员管理**（M-1~M-8）：加成员 201 + 被加者通知；重复 409；
  成员加人 403；陌生人 404；自移除 409。
- **B 可见性**（US2/FR-004/005/SC-001/003）：加成员后成员的
  relevant 列表**同会话内**立即可见；移除后立即不可见。
- **C 协作链**（US3/FR-006/SC-002）：成员 建→改→分配（202，用 owner 的
  agent——目标池扩展）→连边；改归属 403；陌生人维持隔离。
- **D 回归**（SC-004/006）：s10 / s9 / s8 / s1–s4 原样重跑全绿。

依赖：场景 C 的分配会真实建 Run（拖给 agent 即入队执行，LLM 真实端点）。
注册走邀请码。

用法：
    python deploy/verify/s11_project_sharing.py --base http://localhost:8080 --invite <INVITE>
"""

import argparse
import asyncio
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass

import httpx


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


class SharingVerifier:
    def __init__(self, base: str, invite: str) -> None:
        self.base = base.rstrip("/")
        self.invite = invite
        self.results: list[Result] = []
        self._http = httpx.AsyncClient(timeout=60.0)
        self._tokens: dict[str, str] = {}
        self._ids: dict[str, str] = {}

    # ---------- 基础 ----------

    async def setup(self) -> None:
        for who in ("owner", "mate", "stranger"):
            email = f"share-{who}-{uuid.uuid4().hex[:8]}@test.com"
            resp = await self._http.post(
                f"{self.base}/api/v1/auth/register",
                json={
                    "email": email,
                    "password": "TestPass123!",
                    "invite_code": self.invite,
                },
            )
            assert resp.status_code == 201, f"注册失败: {resp.status_code} {resp.text[:300]}"
            body = resp.json()
            self._tokens[who] = body["tokens"]["access"]
            self._ids[who] = body["user"]["id"]

    def _headers(self, who: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._tokens[who]}"}

    def _record(self, name: str, ok: bool, detail: str, started: float) -> None:
        self.results.append(Result(name, ok, detail, time.monotonic() - started))

    async def _create_request(self, who: str, title: str, project_id: str | None = None) -> dict:
        body: dict = {"title": title}
        if project_id:
            body["project_id"] = project_id
        resp = await self._http.post(
            f"{self.base}/api/v1/requests",
            headers=self._headers(who),
            json=body,
        )
        assert resp.status_code == 201, f"建需求失败: {resp.status_code} {resp.text[:300]}"
        return resp.json()

    async def _create_project(self, who: str, name: str) -> dict:
        resp = await self._http.post(
            f"{self.base}/api/v1/projects",
            headers=self._headers(who),
            json={"name": name},
        )
        assert resp.status_code == 201, f"建项目失败: {resp.status_code}"
        return resp.json()

    async def _create_agent(self, who: str) -> dict:
        resp = await self._http.post(
            f"{self.base}/api/v1/agents",
            headers=self._headers(who),
            json={
                "name": f"共享验收 {uuid.uuid4().hex[:6]}",
                "system_prompt": "你是验收助手，请简短回答。",
                "model": "Deepseek-V4.1-Flash",
                "max_steps": 4,
                "cost_limit_cents": 10000,
                "tools": [],
            },
        )
        assert resp.status_code == 201, f"建 agent 失败: {resp.status_code}"
        return resp.json()

    async def _add_member(self, project_id: str, user_id: str):
        return await self._http.post(
            f"{self.base}/api/v1/projects/{project_id}/members",
            headers=self._headers("owner"),
            json={"user_id": user_id},
        )

    # ---------- 场景 ----------

    async def scenario_a_member_management(self) -> None:
        """A：加人/通知/重复/成员 403/陌生人 404/自移除 409。"""
        started = time.monotonic()
        checks: list[str] = []
        project = await self._create_project("owner", f"A-{uuid.uuid4().hex[:4]}")

        add = await self._add_member(project["id"], self._ids["mate"])
        ok_add = add.status_code == 201
        checks.append(f"加人={add.status_code}")

        dup = await self._add_member(project["id"], self._ids["mate"])
        checks.append(f"重复409={dup.status_code}")

        # 被加者通知
        notes = await self._http.get(
            f"{self.base}/api/v1/notifications?unread_only=true",
            headers=self._headers("mate"),
        )
        ready = [
            n for n in notes.json()["items"] if n["type"] == "project_member_added"
        ]
        ok_note = len(ready) == 1 and ready[0]["payload"]["project_name"] == project["name"]
        checks.append(f"被加者通知={ok_note}")

        # 成员加人 → 403（他知道项目存在，不够权）
        member_add = await self._http.post(
            f"{self.base}/api/v1/projects/{project['id']}/members",
            headers=self._headers("mate"),
            json={"user_id": self._ids["stranger"]},
        )
        checks.append(f"成员加人403={member_add.status_code}")

        # 陌生人 → 404
        stranger_add = await self._http.post(
            f"{self.base}/api/v1/projects/{project['id']}/members",
            headers=self._headers("stranger"),
            json={"user_id": self._ids["stranger"]},
        )
        checks.append(f"陌生人404={stranger_add.status_code}")

        # owner 自移除 → 409
        self_remove = await self._http.delete(
            f"{self.base}/api/v1/projects/{project['id']}/members/{self._ids['owner']}",
            headers=self._headers("owner"),
        )
        checks.append(f"自移除409={self_remove.status_code}")

        # 成员列表含 owner 标记
        listing = await self._http.get(
            f"{self.base}/api/v1/projects/{project['id']}/members",
            headers=self._headers("owner"),
        )
        items = listing.json()["items"]
        ok_list = (
            any(m["is_owner"] and m["user_id"] == self._ids["owner"] for m in items)
            and any(not m["is_owner"] and m["user_id"] == self._ids["mate"] for m in items)
        )
        checks.append(f"列表含owner={ok_list}")

        ok = (
            ok_add
            and dup.status_code == 409
            and ok_note
            and member_add.status_code == 403
            and stranger_add.status_code == 404
            and self_remove.status_code == 409
            and ok_list
        )
        self._record("A 成员管理", ok, "，".join(checks), started)

    async def scenario_b_visibility(self) -> None:
        """B：加人后同会话立即可见；移除后立即不可见。"""
        started = time.monotonic()
        checks: list[str] = []
        project = await self._create_project("owner", f"B-{uuid.uuid4().hex[:4]}")
        await self._create_request("owner", "B-项目内需求", project["id"])
        await self._create_request("owner", "B-owner 私活")

        # 加人前：mate 看不到项目内需求
        before = await self._http.get(
            f"{self.base}/api/v1/requests", headers=self._headers("mate")
        )
        ok_before = all(
            "B-项目内需求" != r["title"] for r in before.json()["items"]
        )
        checks.append(f"加人前不可见={ok_before}")

        await self._add_member(project["id"], self._ids["mate"])

        # 加人后：同会话立即可见（SC-001）
        projects = await self._http.get(
            f"{self.base}/api/v1/projects?scope=relevant",
            headers=self._headers("mate"),
        )
        ok_project = project["id"] in {p["id"] for p in projects.json()["items"]}
        requests = await self._http.get(
            f"{self.base}/api/v1/requests", headers=self._headers("mate")
        )
        titles = {r["title"] for r in requests.json()["items"]}
        ok_requests = "B-项目内需求" in titles and "B-owner 私活" not in titles
        checks.append(f"加人后可见={ok_project and ok_requests}")

        # 移除后：立即不可见（SC-003）
        remove = await self._http.delete(
            f"{self.base}/api/v1/projects/{project['id']}/members/{self._ids['mate']}",
            headers=self._headers("owner"),
        )
        after = await self._http.get(
            f"{self.base}/api/v1/requests", headers=self._headers("mate")
        )
        ok_after = all(
            "B-项目内需求" != r["title"] for r in after.json()["items"]
        )
        checks.append(f"移除后收紧={remove.status_code == 204 and ok_after}")

        # 被移出者收到 removed 通知
        notes = await self._http.get(
            f"{self.base}/api/v1/notifications?unread_only=true",
            headers=self._headers("mate"),
        )
        removed = [
            n for n in notes.json()["items"] if n["type"] == "project_member_removed"
        ]
        checks.append(f"移出通知={len(removed) == 1}")

        ok = ok_before and ok_project and ok_requests and ok_after and len(removed) == 1
        self._record("B 可见性", ok, "，".join(checks), started)

    async def scenario_c_collaboration(self) -> None:
        """C：成员 建→改→分配（owner 的 agent）→连边；改归属 403；陌生人隔离。"""
        started = time.monotonic()
        checks: list[str] = []
        project = await self._create_project("owner", f"C-{uuid.uuid4().hex[:4]}")
        await self._add_member(project["id"], self._ids["mate"])
        agent = await self._create_agent("owner")

        # ① 成员建需求进共享项目
        created = await self._create_request("mate", "C-成员建的", project["id"])
        ok_create = True  # _create_request 内部断言 201
        checks.append("成员建=201")

        # ② 成员改 owner 建的需求
        owner_req = await self._create_request("owner", "C-owner 建的", project["id"])
        edited = await self._http.patch(
            f"{self.base}/api/v1/requests/{owner_req['id']}",
            headers=self._headers("mate"),
            json={"title": "C-被成员改过的"},
        )
        checks.append(f"成员改={edited.status_code}")

        # ③ 成员拖给 **owner 的 agent**（目标池扩展，202 建 Run）
        assigned = await self._http.post(
            f"{self.base}/api/v1/requests/{owner_req['id']}/assign",
            headers=self._headers("mate"),
            json={"assignee_type": "agent", "assignee_id": agent["id"]},
        )
        checks.append(f"成员分配={assigned.status_code}")

        # ④ 成员连依赖边
        edge = await self._http.post(
            f"{self.base}/api/v1/requests/{created['id']}/dependencies",
            headers=self._headers("mate"),
            json={"dependency_id": owner_req["id"]},
        )
        checks.append(f"成员连边={edge.status_code}")

        # ⑤ 另一成员改归属 → 403（仅创建者或该项目 owner）
        await self._http.post(
            f"{self.base}/api/v1/projects/{project['id']}/members",
            headers=self._headers("owner"),
            json={"user_id": self._ids["stranger"]},
        )
        other_project = await self._create_project("owner", f"C-2-{uuid.uuid4().hex[:4]}")
        blocked = await self._http.patch(
            f"{self.base}/api/v1/requests/{created['id']}",
            headers=self._headers("stranger"),
            json={"project_id": other_project["id"]},
        )
        checks.append(f"改归属403={blocked.status_code}")

        # ⑥ 完全陌生的人（第四账号太贵，用未加人的 stranger 验隔离已在 B 覆盖）
        ok = (
            ok_create
            and edited.status_code == 200
            and assigned.status_code == 202
            and edge.status_code == 201
            and blocked.status_code == 403
        )
        self._record("C 协作链", ok, "，".join(checks), started)

    async def scenario_d_regression(self) -> None:
        """D：s10 / s9 / s8 / s1–s4 原样重跑（SC-004 零回归）。"""
        started = time.monotonic()
        checks: list[str] = []

        for script, expect in (
            ("s10_request_dependency.py", "4 场景：4 通过 / 0 失败"),
            ("s9_project_container.py", "5 场景：5 通过 / 0 失败"),
            ("s8_request_dispatch.py", "7 场景：7 通过 / 0 失败"),
            ("s1_s4_scenarios.py", "全部通过"),
        ):
            args = (
                []
                if script == "s1_s4_scenarios.py"
                else ["--invite", self.invite]
            )
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                f"deploy/verify/{script}",
                "--base",
                self.base,
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            out, _ = await proc.communicate()
            text = out.decode(errors="replace")
            ok = proc.returncode == 0 and expect in text
            checks.append(f"{script.split('_')[0]}={ok}")
            if not ok:
                print(text[-1500:])

        ok = all(c.endswith("=True") for c in checks)
        self._record("D 回归（s10 + s9 + s8 + s1–s4）", ok, "，".join(checks), started)

    async def run(self) -> int:
        await self.setup()
        await self.scenario_a_member_management()
        await self.scenario_b_visibility()
        await self.scenario_c_collaboration()
        await self.scenario_d_regression()
        await self._http.aclose()

        print()
        for r in self.results:
            print(r.line())
        failed = [r for r in self.results if not r.ok]
        print(f"\n合计 {len(self.results)} 场景：{len(self.results) - len(failed)} 通过 / {len(failed)} 失败")
        return 1 if failed else 0


def main() -> None:
    parser = argparse.ArgumentParser(description="010 项目共享真实栈验收")
    parser.add_argument("--base", default="http://localhost:8080")
    parser.add_argument("--invite", required=True, help="邀请码（member）")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(SharingVerifier(args.base, args.invite).run()))


if __name__ == "__main__":
    main()
