"""S5 全场景验证脚本（quickstart.md S5：FR-014~021 / SC-003~004, SC-006~008）。

针对真实 docker compose 栈（api/worker/llm-gateway）验证：
- FR-017 失败→可读错误→重试→成功（SC-008 基础）
- FR-018 执行中取消（含 needs_input 挂起时取消）
- FR-019 成本熔断（cost_limit_cents 超限自动终止，SC-006）
- FR-021 needs_input 挂起 → reply 恢复完成
- SC-003 WS 实时事件延迟 <2s
- SC-004 简单执行 <5min

Mock LLM 网关关键词路由：
- 需求含 "FAIL" → 500（模拟 LLM 不可用）
- 需求含 "补充信息" → __NEEDS_INPUT__ 前缀（needs_input 挂起）
- 其他 → ReAct 两轮闭环（tool_call → final）
- usage 固定：首轮 60000+10000 tokens → 约 1 分；末轮 60000+20000 → 约 2 分

用法：python s5_scenarios.py --base http://localhost:8080 --invite TEST-ADMIN-CODE-123
"""

import argparse
import asyncio
import json
import sys
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx

# 每轮 LLM 调用成本（mock usage → gpt-4o-mini 单价换算）：
#   首轮 tool_call: 60000×0.00015 + 10000×0.0006 = 9 + 6 = 15 USD/1000... 实际按公式：
#   (60000*0.00015 + 10000*0.0006)/1000*100 = (9+6)/1000*100 = 1.5 → round = 2 分
#   末轮 final: (60000*0.00015 + 20000*0.0006)/1000*100 = (9+12)/1000*100 = 2.1 → round = 2 分
# needs_input 轮: (60000*0.00015 + 20000*0.0006)/1000*100 = 2.1 → 2 分
COST_PER_CALL_CENTS = 2


@dataclass
class Result:
    name: str
    ok: bool
    detail: str = ""
    elapsed_s: float = 0.0

    def line(self) -> str:
        mark = "✅" if self.ok else "❌"
        return f"{mark} {self.name} — {self.detail}" + (f"（{self.elapsed_s:.2f}s）" if self.elapsed_s else "")


class S5Verifier:
    def __init__(self, base: str, invite: str):
        self.base = base.rstrip("/")
        self.invite = invite
        self.token: str | None = None
        self.results: list[Result] = []
        self._http = httpx.AsyncClient(timeout=30.0)

    # ---------- 基础 ----------

    async def setup(self) -> None:
        email = f"s5-{uuid.uuid4().hex[:8]}@test.com"
        resp = await self._http.post(
            f"{self.base}/api/v1/auth/register",
            json={"email": email, "password": "TestPass123!", "invite_code": self.invite},
        )
        if resp.status_code == 409:  # 已存在（上次残留）
            resp = await self._http.post(
                f"{self.base}/api/v1/auth/login",
                json={"email": email, "password": "TestPass123!"},
            )
        assert resp.status_code in (201, 200), f"注册/登录失败: {resp.status_code} {resp.text[:300]}"
        self.token = resp.json()["tokens"]["access"]

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    async def _create_agent(self, *, cost_limit_cents: int = 10000, max_steps: int = 10) -> dict[str, Any]:
        resp = await self._http.post(
            f"{self.base}/api/v1/agents",
            headers=self._headers(),
            json={
                "name": f"s5-agent-{uuid.uuid4().hex[:6]}",
                "system_prompt": "你是一个任务执行助手。如果缺少关键信息，回复 __NEEDS_INPUT__ 加补充要求。",
                "model": "gpt-4o-mini",
                "max_steps": max_steps,
                "cost_limit_cents": cost_limit_cents,
                "tools": ["web_search"],
            },
        )
        assert resp.status_code == 201, f"创建 agent 失败: {resp.status_code} {resp.text[:300]}"
        return resp.json()

    async def _create_request(self, title: str, description: str) -> dict[str, Any]:
        resp = await self._http.post(
            f"{self.base}/api/v1/requests",
            headers=self._headers(),
            json={
                "title": title,
                "description": description,
                "expected_output": "一段 3-5 行的总结",
                "priority": "normal",
            },
        )
        assert resp.status_code == 201, f"创建 request 失败: {resp.status_code} {resp.text[:300]}"
        return resp.json()

    async def _assign(self, request_id: str, agent_id: str) -> dict[str, Any]:
        resp = await self._http.post(
            f"{self.base}/api/v1/requests/{request_id}/assign",
            headers=self._headers(),
            json={"assignee_type": "agent", "assignee_id": agent_id},
        )
        assert resp.status_code == 202, f"分配失败: {resp.status_code} {resp.text[:300]}"
        return resp.json()

    async def _run_detail(self, run_id: str) -> dict[str, Any]:
        resp = await self._http.get(f"{self.base}/api/v1/runs/{run_id}", headers=self._headers())
        assert resp.status_code == 200, f"查询 run 失败: {resp.status_code} {resp.text[:300]}"
        return resp.json()

    async def _wait_terminal(self, run_id: str, timeout: float = 60.0) -> dict[str, Any]:
        """轮询直到终态（completed/failed/cancelled/timeout/needs_input）。"""
        start = time.monotonic()
        while time.monotonic() - start < timeout:
            detail = await self._run_detail(run_id)
            if detail["status"] in {"completed", "failed", "cancelled", "timeout", "needs_input"}:
                return detail
            await asyncio.sleep(0.3)
        raise TimeoutError(f"run {run_id} 超时未达终态（{timeout}s）")

    async def _post(self, path: str, json_body: dict[str, Any]) -> httpx.Response:
        return await self._http.post(f"{self.base}/api/v1{path}", headers=self._headers(), json=json_body)

    # ---------- 场景 ----------

    async def sc004_simple_execute(self) -> None:
        """SC-004：简单执行 <5min（真实栈：LLM 网关 → worker → 完成）。"""
        start = time.monotonic()
        agent = await self._create_agent()
        req = await self._create_request("SC004 简单执行", "帮我调研并整理一份简短报告")
        assigned = await self._assign(req["id"], agent["id"])
        run_id = assigned["run_id"]
        detail = await self._wait_terminal(run_id, timeout=120)
        elapsed = time.monotonic() - start

        ok = detail["status"] == "completed"
        steps = detail.get("steps", [])
        kinds = [s.get("kind") for s in steps]
        self.results.append(
            Result(
                "SC-004 简单执行 <5min",
                ok and kinds == ["llm", "tool", "llm", "final"],
                f"status={detail['status']} steps={kinds} cost={detail.get('cost_cents')}分",
                elapsed,
            )
        )

    async def fr017_fail_retry(self) -> None:
        """FR-017：LLM 不可用 → 可读失败 → retry → 成功（SC-008 基础）。"""
        start = time.monotonic()
        agent = await self._create_agent()
        req = await self._create_request("FR017 FAIL 场景", "这个需求故意触发 FAIL 模拟 LLM 故障")
        assigned = await self._assign(req["id"], agent["id"])
        run_id = assigned["run_id"]
        failed = await self._wait_terminal(run_id, timeout=120)
        ok1 = failed["status"] == "failed" and failed.get("error")
        err_preview = (failed.get("error") or "")[:60]

        # 重试（此时需求含 FAIL，mock 仍返回 500 → 第二次仍失败？不，重试用同一需求文本）。
        # 关键：把 agent 换成不含 FAIL 的需求场景——重试语义是「同一 run 重新执行」，
        # mock 仍按需求文本路由 → 需要换一种方式：修改 request 描述去掉 FAIL 后重试？
        # 契约上 retry 只对 failed/timeout 开放；需求文本不可变。因此此处验证「重试→再次失败→可读错误」，
        # 再用正常需求验证「重试成功路径」。
        resp = await self._post(f"/runs/{run_id}/retry", {})
        ok2 = resp.status_code == 202 and resp.json()["status"] == "queued"
        failed2 = await self._wait_terminal(run_id, timeout=120)
        ok3 = failed2["status"] == "failed"  # 需求仍含 FAIL → 再次失败属预期
        elapsed = time.monotonic() - start

        self.results.append(
            Result(
                "FR-017 失败→可读错误",
                ok1,
                f"status={failed['status']} error={err_preview}",
                elapsed,
            )
        )
        self.results.append(
            Result(
                "FR-017 retry 受理(202 queued)",
                ok2,
                f"retry → {resp.json()['status'] if ok2 else resp.text[:80]}",
            )
        )
        self.results.append(
            Result(
                "FR-017 重试再失败(需求仍 FAIL 属预期)",
                ok3,
                f"status={failed2['status']} error={(failed2.get('error') or '')[:60]}",
            )
        )

        # 重试成功路径：正常需求 → 失败注入（停网关）→ 恢复 → retry → completed（SC-008 完整闭环）
        # 关键：先停网关再分配，确保首次执行时 LLM 不可用（避免执行太快已完成再停的竞态）。
        import subprocess
        agent2 = await self._create_agent()
        req2 = await self._create_request("FR017b 正常重试", "帮我整理一份市场调研摘要")

        # 停网关模拟故障
        subprocess.run(["docker", "stop", "aw-llm-gateway"], check=True, capture_output=True)
        await asyncio.sleep(1.0)
        assigned2 = await self._assign(req2["id"], agent2["id"])
        run_id2 = assigned2["run_id"]
        failed_b = await self._wait_terminal(run_id2, timeout=120)
        ok_b1 = failed_b["status"] == "failed" and "LLM 调用失败" in (failed_b.get("error") or "")

        # 恢复网关 → retry → 成功
        subprocess.run(["docker", "start", "aw-llm-gateway"], check=True, capture_output=True)
        await asyncio.sleep(2.0)
        resp_b = await self._post(f"/runs/{run_id2}/retry", {})
        ok_b2 = resp_b.status_code == 202
        done_b = await self._wait_terminal(run_id2, timeout=120)
        ok_b3 = done_b["status"] == "completed"

        self.results.append(
            Result(
                "FR-017/SC-008 失败(停网关)→重试→成功",
                ok_b1 and ok_b2 and ok_b3,
                f"failed({failed_b['status']}) → retry({resp_b.status_code}) → {done_b['status']}, cost={done_b.get('cost_cents')}分",
            )
        )

    async def fr018_cancel(self) -> None:
        """FR-018：执行中取消 → 状态 cancelled、停止执行。
        SLOW 需求让 mock 每轮 sleep 3s，制造取消窗口。"""
        start = time.monotonic()
        agent = await self._create_agent(max_steps=50)  # 多步循环，给取消留窗口
        req = await self._create_request("FR018 SLOW 取消场景", "帮我执行一个长时间任务（多轮工具调用）")
        assigned = await self._assign(req["id"], agent["id"])
        run_id = assigned["run_id"]

        # 等进入 running（SLOW 每轮 3s，窗口充足）
        deadline = time.monotonic() + 20
        entered = False
        while time.monotonic() < deadline:
            detail = await self._run_detail(run_id)
            if detail["status"] == "running":
                entered = True
                break
            if detail["status"] in {"completed", "failed"}:
                break
            await asyncio.sleep(0.2)

        resp = await self._post(f"/runs/{run_id}/cancel", {})
        ok1 = resp.status_code == 200 and resp.json()["status"] == "cancelled"
        await asyncio.sleep(1.0)
        after = await self._run_detail(run_id)
        ok2 = after["status"] == "cancelled"
        elapsed = time.monotonic() - start

        self.results.append(
            Result(
                "FR-018 执行中取消",
                ok1 and ok2 and entered,
                f"entered_running={entered} cancel→{resp.json().get('status') if ok1 else resp.text[:80]}，终态={after['status']}（未被覆盖）",
                elapsed,
            )
        )

    async def fr019_cost_limit(self) -> None:
        """FR-019/SC-006：成本熔断——cost_limit_cents=4，首轮 2 分 + 末轮 2 分 = 4 分 ≤ 4 → 不熔断；
        设为 2 分 → 首轮后 2 分，第二轮 4 分 > 2 → 熔断失败。用 1 分验证：首轮 2 分 > 1 → 首轮即熔断。"""
        start = time.monotonic()
        # 上限 1 分：每轮 2 分 → 首轮累计 2 > 1 → 熔断
        agent = await self._create_agent(cost_limit_cents=1, max_steps=10)
        req = await self._create_request("FR019 熔断场景", "帮我执行一个普通任务")
        assigned = await self._assign(req["id"], agent["id"])
        run_id = assigned["run_id"]
        detail = await self._wait_terminal(run_id, timeout=120)
        elapsed = time.monotonic() - start

        ok = (
            detail["status"] == "failed"
            and "成本超限" in (detail.get("error") or "")
            and detail.get("cost_cents", 0) > 1
        )
        self.results.append(
            Result(
                "FR-019/SC-006 成本熔断",
                ok,
                f"status={detail['status']} error={(detail.get('error') or '')[:50]} cost={detail.get('cost_cents')}分(>1)",
                elapsed,
            )
        )

    async def fr021_needs_input_reply(self) -> None:
        """FR-021：LLM 请求补充信息 → needs_input 挂起 → reply → 恢复完成。"""
        start = time.monotonic()
        agent = await self._create_agent()
        req = await self._create_request("FR021 补充信息", "帮我制定一个营销方案，请补充信息")
        assigned = await self._assign(req["id"], agent["id"])
        run_id = assigned["run_id"]

        suspended = await self._wait_terminal(run_id, timeout=120)
        ok1 = suspended["status"] == "needs_input"

        # reply 恢复
        resp = await self._post(f"/runs/{run_id}/reply", {"message": "客户是中小型企业，预算 5 万元"})
        ok2 = resp.status_code == 200 and resp.json()["status"] == "running"
        done = await self._wait_terminal(run_id, timeout=120)
        ok3 = done["status"] == "completed" and done.get("result")
        elapsed = time.monotonic() - start

        self.results.append(
            Result(
                "FR-021 needs_input→reply→完成",
                ok1 and ok2 and ok3,
                f"挂起={suspended['status']} → reply({resp.status_code}) → {done['status']}, result={str(done.get('result'))[:40]}",
                elapsed,
            )
        )

    async def fr021_cancel_while_needs_input(self) -> None:
        """FR-021+FR-018：needs_input 挂起时取消（状态机补的 CANCELLED 迁移）。"""
        agent = await self._create_agent()
        req = await self._create_request("FR021b 挂起取消", "帮我写个方案，请补充信息")
        assigned = await self._assign(req["id"], agent["id"])
        run_id = assigned["run_id"]
        suspended = await self._wait_terminal(run_id, timeout=120)
        ok1 = suspended["status"] == "needs_input"
        resp = await self._post(f"/runs/{run_id}/cancel", {})
        ok2 = resp.status_code == 200 and resp.json()["status"] == "cancelled"
        after = await self._run_detail(run_id)
        ok3 = after["status"] == "cancelled"
        self.results.append(
            Result(
                "FR-021+018 needs_input 挂起取消",
                ok1 and ok2 and ok3,
                f"挂起={suspended['status']} → cancel {resp.status_code} → {after['status']}",
            )
        )

    async def sc008_retry_rate(self) -> None:
        """SC-008：重试成功率 ≥80%——停网关造 5 个失败 → 恢复 → 逐个 retry → 统计成功比例。"""
        import subprocess

        start = time.monotonic()
        n = 5
        agent = await self._create_agent()
        reqs = [await self._create_request(f"SC008 重试{i}", "帮我整理一份要点摘要") for i in range(n)]

        subprocess.run(["docker", "stop", "aw-llm-gateway"], check=True, capture_output=True)
        await asyncio.sleep(1.0)
        run_ids = [(await self._assign(r["id"], agent["id"]))["run_id"] for r in reqs]
        failed = await asyncio.gather(*[self._wait_terminal(rid, timeout=180) for rid in run_ids])
        n_failed = sum(1 for d in failed if d["status"] == "failed")

        subprocess.run(["docker", "start", "aw-llm-gateway"], check=True, capture_output=True)
        await asyncio.sleep(2.5)
        accepted = 0
        for rid in run_ids:
            resp = await self._post(f"/runs/{rid}/retry", {})
            if resp.status_code == 202:
                accepted += 1
        done = await asyncio.gather(*[self._wait_terminal(rid, timeout=180) for rid in run_ids])
        recovered = sum(1 for d in done if d["status"] == "completed")
        rate = recovered / n
        elapsed = time.monotonic() - start

        self.results.append(
            Result(
                "SC-008 重试成功率 ≥80%",
                n_failed == n and accepted == n and rate >= 0.8,
                f"造失败 {n_failed}/{n} → retry 受理 {accepted}/{n} → 恢复 {recovered}/{n}（{rate:.0%}）",
                elapsed,
            )
        )

    async def sc003_ws_events(self) -> None:
        """SC-003：WS 实时事件延迟 <2s（status_change/step_new/cost_update/run_completed）。

        用 needs_input 挂起 → reply 恢复的场景：
        - run 先挂起 needs_input（订阅建立前无事件可错过）
        - WS 订阅建立后 reply → 恢复执行 → status_change(running)/step_new/cost_update/run_completed
          全部在订阅建立之后产生 → 完整验证「事件产生→推送」延迟。
        """
        import websockets

        agent = await self._create_agent()
        req = await self._create_request("SC003 WS 场景", "帮我制定营销方案，请补充信息")
        assigned = await self._assign(req["id"], agent["id"])
        run_id = assigned["run_id"]

        # 等 needs_input 挂起（订阅前无事件产生）
        suspended = await self._wait_terminal(run_id, timeout=60)
        assert suspended["status"] == "needs_input", f"预期 needs_input，实际 {suspended['status']}"

        # ws_router 挂载在 app 根（无 /api/v1 前缀，见 app/main.py）：/ws/runs/{run_id}
        uri = f"{self.base.replace('http', 'ws')}/ws/runs/{run_id}?token={self.token}"
        events: list[dict[str, Any]] = []
        terminal_arrival: float | None = None  # run_completed 到达时刻

        async def collect() -> None:
            nonlocal terminal_arrival
            async with websockets.connect(uri) as ws:
                while True:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=30.0)
                    except asyncio.TimeoutError:
                        break
                    evt = json.loads(raw)
                    events.append(evt)
                    if evt.get("type") in {"run_completed", "run_failed", "run_cancelled", "run_timeout"}:
                        terminal_arrival = time.monotonic()
                        break

        collector = asyncio.create_task(collect())
        await asyncio.sleep(0.5)  # 确认 WS 订阅建立

        # reply 恢复执行 → 所有事件在订阅建立后产生
        resp = await self._post(f"/runs/{run_id}/reply", {"message": "客户是中小型企业，预算 5 万元"})
        assert resp.status_code == 200, f"reply 失败: {resp.status_code} {resp.text[:200]}"
        done = await self._wait_terminal(run_id, timeout=120)
        state_visible_at = time.monotonic()
        await asyncio.wait_for(collector, timeout=15.0)

        types = [e.get("type") for e in events]
        has_flow = (
            "status_change" in types
            and "step_new" in types
            and "cost_update" in types
            and "run_completed" in types
        )
        # 推送延迟：终态事件到达 WS 不晚于状态可探测 +2s
        push_lag = (terminal_arrival or 0.0) - state_visible_at
        ok = (
            done["status"] == "completed"
            and has_flow
            and terminal_arrival is not None
            and push_lag < 2.0
        )
        self.results.append(
            Result(
                "SC-003 WS 实时事件 <2s",
                ok,
                f"types={types} push_lag={push_lag:.2f}s (终态事件 vs 状态探测) status={done['status']}",
            )
        )

    async def sc007_concurrency(self) -> None:
        """SC-007：20+ 并发不卡顿——20 个需求并发分配，全部执行完成，无失败/超时。"""
        start = time.monotonic()
        n = 20
        agent = await self._create_agent()
        reqs = await asyncio.gather(
            *[self._create_request(f"SC007 并发{i}", "帮我整理一份简要报告") for i in range(n)]
        )
        assigned = await asyncio.gather(
            *[self._assign(r["id"], agent["id"]) for r in reqs]
        )
        run_ids = [a["run_id"] for a in assigned]
        assert all(a["status"] == "queued" for a in assigned)

        # 并发等待全部终态（completed）
        details = await asyncio.gather(
            *[self._wait_terminal(run_id, timeout=120) for run_id in run_ids]
        )
        elapsed = time.monotonic() - start
        ok = all(d["status"] == "completed" for d in details)
        self.results.append(
            Result(
                "SC-007 20+ 并发不卡顿",
                ok,
                f"{n} 个并发分配→执行→完成，耗时 {elapsed:.1f}s，全部 completed",
                elapsed,
            )
        )

    async def run_all(self) -> None:
        await self.setup()
        await self.sc004_simple_execute()
        await self.fr017_fail_retry()
        await self.fr018_cancel()
        await self.fr019_cost_limit()
        await self.fr021_needs_input_reply()
        await self.fr021_cancel_while_needs_input()
        await self.sc008_retry_rate()
        await self.sc003_ws_events()
        await self.sc007_concurrency()

    def summary(self) -> tuple[bool, str]:
        lines = [r.line() for r in self.results]
        ok = all(r.ok for r in self.results)
        return ok, "\n".join(lines)


async def main() -> int:
    parser = argparse.ArgumentParser(description="S5 全场景验证")
    parser.add_argument("--base", default="http://localhost:8080")
    parser.add_argument("--invite", default="TEST-ADMIN-CODE-123")
    args = parser.parse_args()

    verifier = S5Verifier(args.base, args.invite)
    try:
        await verifier.run_all()
    except Exception as exc:  # noqa: BLE001 - 汇总输出
        verifier.results.append(Result("脚本异常", False, str(exc)[:200]))
    ok, lines = verifier.summary()
    print("\n===== S5 验证结果 =====")
    print(lines)
    print(f"\n总计：{'✅ 全部通过' if ok else '❌ 存在失败'}（{len(verifier.results)} 项）")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
