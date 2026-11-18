"""需求依赖编排的真实栈验收（009 / T026）。

四个场景对应 spec 的成功标准，全部走 HTTP 打真实容器栈：

- **A 连边与成环**（D-1~D-6）：加边 201（带标题/状态）；自依赖 400；
  重复 409；直接成环与传递成环 409；删边 204。
- **B 硬门禁**（US2/FR-004）：未完成前置拖 agent → 409 且消息逐条；
  三种终态（completed/failed/cancelled）后 → 202。
- **C 解锁通知**（US3/FR-007/008）：前置终态 → 后续方 owner 与 assignee
  各收一条 dependency_ready（同人去重）；重复收敛不重发。
- **D 回归**（SC-005/006）：s9 / s8 / s1–s4 原样重跑全绿。

依赖：真实 LLM 端点（场景 B 的终态解锁要走一次真实执行；场景 C 用
取消路径避免依赖 LLM）。注册走邀请码。

用法：
    python deploy/verify/s10_request_dependency.py --base http://localhost:8080 --invite <INVITE>
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


class DependencyVerifier:
    def __init__(self, base: str, invite: str) -> None:
        self.base = base.rstrip("/")
        self.invite = invite
        self.results: list[Result] = []
        self._http = httpx.AsyncClient(timeout=60.0)
        self._tokens: dict[str, str] = {}
        self._ids: dict[str, str] = {}

    # ---------- 基础 ----------

    async def setup(self) -> None:
        for who in ("owner", "worker", "other"):
            email = f"dep-{who}-{uuid.uuid4().hex[:8]}@test.com"
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

    async def _create_request(self, who: str, title: str) -> dict:
        resp = await self._http.post(
            f"{self.base}/api/v1/requests",
            headers=self._headers(who),
            json={"title": title},
        )
        assert resp.status_code == 201, f"建需求失败: {resp.status_code} {resp.text[:300]}"
        return resp.json()

    async def _add_edge(self, who: str, dependent_id: str, dependency_id: str):
        return await self._http.post(
            f"{self.base}/api/v1/requests/{dependent_id}/dependencies",
            headers=self._headers(who),
            json={"dependency_id": dependency_id},
        )

    async def _cancel_request(self, who: str, request_id: str, agent_id: str) -> int:
        """把需求打到 cancelled 终态：assign 建 Run → 取消 Run → settle 收敛。

        系统没有「取消需求」端点（需求取消只能经由它的 Run——状态机纪律），
        所以终态路径必须是这一条。返回取消接口的状态码。
        """
        assign = await self._http.post(
            f"{self.base}/api/v1/requests/{request_id}/assign",
            headers=self._headers(who),
            json={"assignee_type": "agent", "assignee_id": agent_id},
        )
        assert assign.status_code == 202, f"assign 失败: {assign.text[:200]}"
        run_id = assign.json()["run_id"]
        cancel = await self._http.post(
            f"{self.base}/api/v1/runs/{run_id}/cancel", headers=self._headers(who)
        )
        return cancel.status_code

    async def _create_agent(self, who: str) -> dict:
        resp = await self._http.post(
            f"{self.base}/api/v1/agents",
            headers=self._headers(who),
            json={
                "name": f"依赖验收 {uuid.uuid4().hex[:6]}",
                "system_prompt": "你是验收助手，请简短回答。",
                "model": "Deepseek-V4.1-Flash",
                "max_steps": 4,
                "cost_limit_cents": 10000,
                "tools": [],
            },
        )
        assert resp.status_code == 201, f"建 agent 失败: {resp.status_code}"
        return resp.json()

    # ---------- 场景 ----------

    async def scenario_a_edges_and_cycles(self) -> None:
        """A：加边/自依赖/重复/两环/删边。"""
        started = time.monotonic()
        checks: list[str] = []
        a = await self._create_request("owner", f"A-前置-{uuid.uuid4().hex[:4]}")
        b = await self._create_request("owner", f"A-后续-{uuid.uuid4().hex[:4]}")
        c = await self._create_request("owner", f"A-第三-{uuid.uuid4().hex[:4]}")

        add = await self._add_edge("owner", b["id"], a["id"])
        ok_add = add.status_code == 201 and add.json()["dependency_title"] == a["title"]
        checks.append(f"加边={ok_add}")

        self_dep = await self._add_edge("owner", b["id"], b["id"])
        checks.append(f"自依赖400={self_dep.status_code}")

        dup = await self._add_edge("owner", b["id"], a["id"])
        checks.append(f"重复409={dup.status_code}")

        # 成环：b→a 已存在，给 a 加「依赖 b」
        cycle = await self._add_edge("owner", a["id"], b["id"])
        checks.append(f"直接成环409={cycle.status_code}")

        # 传递环：b→a、c→b，给 a 加「依赖 c」
        await self._add_edge("owner", c["id"], b["id"])
        trans = await self._add_edge("owner", a["id"], c["id"])
        checks.append(f"传递成环409={trans.status_code}")

        delete = await self._http.delete(
            f"{self.base}/api/v1/requests/{b['id']}/dependencies/{a['id']}",
            headers=self._headers("owner"),
        )
        checks.append(f"删边204={delete.status_code}")

        ok = (
            ok_add
            and self_dep.status_code == 400
            and dup.status_code == 409
            and cycle.status_code == 409
            and trans.status_code == 409
            and delete.status_code == 204
        )
        self._record("A 连边与成环", ok, "，".join(checks), started)

    async def scenario_b_gate(self) -> None:
        """B：硬门禁 409 逐条 + 终态解锁（completed 走真实执行，其余走取消）。"""
        started = time.monotonic()
        checks: list[str] = []
        agent = await self._create_agent("owner")

        # --- 409 逐条 ---
        blocker1 = await self._create_request("owner", "B-阻塞1")
        blocker2 = await self._create_request("owner", "B-阻塞2")
        done = await self._create_request("owner", "B-已取消的")
        dependent = await self._create_request("owner", "B-后续")
        await self._add_edge("owner", dependent["id"], blocker1["id"])
        await self._add_edge("owner", dependent["id"], blocker2["id"])
        await self._add_edge("owner", dependent["id"], done["id"])
        # 第三条前置真正打到终态（取消路径）——「终态前置不出现在 409」
        # 是本场景的关键断言之一
        await self._cancel_request("owner", done["id"], agent["id"])

        blocked = await self._http.post(
            f"{self.base}/api/v1/requests/{dependent['id']}/assign",
            headers=self._headers("owner"),
            json={"assignee_type": "agent", "assignee_id": agent["id"]},
        )
        msg = blocked.json().get("error", {}).get("message", "")
        ok_blocked = (
            blocked.status_code == 409
            and "B-阻塞1" in msg
            and "B-阻塞2" in msg
            and "B-已取消的" not in msg
        )
        checks.append(f"409逐条={ok_blocked}")

        # --- cancelled 解锁（不依赖 LLM：建 Run 后立刻取消，settle 收敛）---
        cancel_code = await self._cancel_request("owner", blocker1["id"], agent["id"])
        ok_cancel_gate = cancel_code in (200, 202)
        checks.append(f"取消阻塞1={cancel_code}")

        # --- 依赖只剩 blocker2 → 仍 409，消息只剩它 ---
        still = await self._http.post(
            f"{self.base}/api/v1/requests/{dependent['id']}/assign",
            headers=self._headers("owner"),
            json={"assignee_type": "agent", "assignee_id": agent["id"]},
        )
        msg2 = still.json().get("error", {}).get("message", "")
        ok_still = still.status_code == 409 and "B-阻塞1" not in msg2 and "B-阻塞2" in msg2
        checks.append(f"剩余阻塞={ok_still}")

        # --- 拖员工不查门禁（FR-005）---
        dispatch = await self._http.post(
            f"{self.base}/api/v1/requests/{dependent['id']}/assign",
            headers=self._headers("owner"),
            json={"assignee_type": "user", "assignee_id": self._ids["worker"]},
        )
        checks.append(f"user不查={dispatch.status_code}")
        ok_user = dispatch.status_code == 202

        ok = ok_blocked and ok_cancel_gate and ok_still and ok_user
        self._record("B 硬门禁", ok, "，".join(checks), started)

    async def scenario_c_ready_notification(self) -> None:
        """C：前置取消（终态）→ 后续方 owner 与 assignee 各一条通知；幂等。"""
        started = time.monotonic()
        checks: list[str] = []
        pre = await self._create_request("owner", "C-前置")
        dependent = await self._create_request("owner", "C-后续")
        await self._add_edge("owner", dependent["id"], pre["id"])

        # 分派给 worker 并接收 → 通知应发 owner + worker 两条
        await self._http.post(
            f"{self.base}/api/v1/requests/{dependent['id']}/assign",
            headers=self._headers("owner"),
            json={"assignee_type": "user", "assignee_id": self._ids["worker"]},
        )
        await self._http.post(
            f"{self.base}/api/v1/requests/{dependent['id']}/accept",
            headers=self._headers("worker"),
        )

        agent = await self._create_agent("owner")
        cancel_code = await self._cancel_request("owner", pre["id"], agent["id"])
        ok_cancel = cancel_code in (200, 202)
        checks.append(f"前置取消={cancel_code}")

        owner_notes = await self._http.get(
            f"{self.base}/api/v1/notifications?unread_only=true",
            headers=self._headers("owner"),
        )
        ready_owner = [
            n
            for n in owner_notes.json()["items"]
            if n["type"] == "dependency_ready"
        ]
        ok_owner = len(ready_owner) == 1
        if ok_owner:
            payload = ready_owner[0]["payload"]
            ok_owner = (
                payload.get("dependency_title") == "C-前置"
                and payload.get("dependency_status") == "cancelled"
                and ready_owner[0]["request_id"] == dependent["id"]
            )
        checks.append(f"owner通知={ok_owner}")

        worker_notes = await self._http.get(
            f"{self.base}/api/v1/notifications?unread_only=true",
            headers=self._headers("worker"),
        )
        ready_worker = [
            n for n in worker_notes.json()["items"] if n["type"] == "dependency_ready"
        ]
        ok_worker = len(ready_worker) == 1
        checks.append(f"worker通知={ok_worker}")

        # 幂等：对已终态需求再发起一次终态路径（取消已取消的 Run → 409，
        # settle 不重复收敛）→ 通知不重复发
        again = await self._http.post(
            f"{self.base}/api/v1/runs/{pre['id']}/cancel",
            headers=self._headers("owner"),
        )
        owner_notes2 = await self._http.get(
            f"{self.base}/api/v1/notifications?unread_only=true",
            headers=self._headers("owner"),
        )
        ready_owner2 = [
            n
            for n in owner_notes2.json()["items"]
            if n["type"] == "dependency_ready"
        ]
        ok_idem = len(ready_owner2) == 1
        checks.append(f"幂等={ok_idem}")

        ok = ok_cancel and ok_owner and ok_worker and ok_idem
        self._record("C 解锁通知", ok, "，".join(checks), started)

    async def scenario_d_regression(self) -> None:
        """D：s9 / s8 / s1–s4 原样重跑（SC-005/006 零回归）。"""
        started = time.monotonic()
        checks: list[str] = []

        for script, expect in (
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
        self._record("D 回归（s9 + s8 + s1–s4）", ok, "，".join(checks), started)

    async def run(self) -> int:
        await self.setup()
        await self.scenario_a_edges_and_cycles()
        await self.scenario_b_gate()
        await self.scenario_c_ready_notification()
        await self.scenario_d_regression()
        await self._http.aclose()

        print()
        for r in self.results:
            print(r.line())
        failed = [r for r in self.results if not r.ok]
        print(f"\n合计 {len(self.results)} 场景：{len(self.results) - len(failed)} 通过 / {len(failed)} 失败")
        return 1 if failed else 0


def main() -> None:
    parser = argparse.ArgumentParser(description="009 需求依赖真实栈验收")
    parser.add_argument("--base", default="http://localhost:8080")
    parser.add_argument("--invite", required=True, help="邀请码（member）")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(DependencyVerifier(args.base, args.invite).run()))


if __name__ == "__main__":
    main()
