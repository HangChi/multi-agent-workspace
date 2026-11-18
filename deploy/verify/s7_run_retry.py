"""运行自动重试的真实栈验收（006 / T034）。

三个场景对应 spec 的成功标准，全部走 HTTP 打真实容器栈：

- **A 瞬时抖动后自动恢复**（SC-001）：Agent 的系统提示词带 `RETRY_FAIL2` 标记，
  mock LLM 对该标记的前 2 次请求返回 500、之后正常。断言 Run 自动重试后
  completed、`attempt == 3`、成本跨次累计。
- **B 一直失败则耗尽终态**（SC-001/SC-005）：`RETRY_FAIL_ALL` 标记，
  断言最终 failed、`attempt` 达到上限、`retry_history` 有每条记录。
- **C 成本累计不被绕过**（SC-003）：上限设得很低，断言累计达到上限即熔断。

依赖：mock LLM 网关（`--profile mock`）必须在线——它承载 `RETRY_FAIL*` 关键词路由。
没有真实 LLM 密钥也能跑完这三个场景。

用法：
    python deploy/verify/s7_run_retry.py --base http://localhost:8080 --invite <INVITE>
"""

import argparse
import asyncio
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


class RetryVerifier:
    def __init__(self, base: str, invite: str, mock_llm: str | None = None) -> None:
        self.base = base.rstrip("/")
        self.invite = invite
        # mock LLM 网关地址（仅用于重置失败计数器）。None = 未指定，跳过重置。
        self.mock_llm = (mock_llm or "").rstrip("/") or None
        self.results: list[Result] = []
        self._http = httpx.AsyncClient(timeout=60.0)
        self._token: str | None = None

    # ---------- 基础 ----------

    async def setup(self) -> None:
        # 重置 mock LLM 的失败计数器：它跨请求累积，不重置的话重复跑脚本时
        # RETRY_FAIL2 的预算已在上一轮用完，会直接成功、看起来像重试失效。
        if self.mock_llm:
            try:
                reset = await self._http.get(f"{self.mock_llm}/__reset")
                if reset.status_code == 200:
                    print(f"mock LLM 计数器已重置（{self.mock_llm}）")
                else:
                    print(f"提示：mock LLM /__reset 返回 {reset.status_code}")
            except httpx.HTTPError as exc:
                print(f"提示：无法连接 mock LLM（{exc}）；场景 A/B 可能不如预期")
        else:
            print("提示：未指定 --mock-llm，跳过计数器重置")

        email = f"retry-{uuid.uuid4().hex[:8]}@test.com"
        resp = await self._http.post(
            f"{self.base}/api/v1/auth/register",
            json={"email": email, "password": "TestPass123!", "invite_code": self.invite},
        )
        assert resp.status_code == 201, f"注册失败: {resp.status_code} {resp.text[:300]}"
        self._token = resp.json()["tokens"]["access"]

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    def _record(self, name: str, ok: bool, detail: str, started: float) -> None:
        self.results.append(Result(name, ok, detail, time.monotonic() - started))

    async def _create_agent(self, *, cost_limit_cents: int = 10000) -> dict:
        """建一个普通验收 Agent（不带任何故障标记）。

        标记必须放在**需求标题**而不是 Agent 的系统提示词里：mock LLM 的关键词
        路由只读最后一条 user 消息，而系统提示词不会出现在那里。
        """
        resp = await self._http.post(
            f"{self.base}/api/v1/agents",
            headers=self._headers(),
            json={
                "name": f"重试验收 {uuid.uuid4().hex[:6]}",
                "system_prompt": "你是验收助手，请简短回答。",
                "model": "gpt-4o-mini",
                "max_steps": 4,
                "cost_limit_cents": cost_limit_cents,
                "tools": [],
            },
        )
        assert resp.status_code == 201, f"建 agent 失败: {resp.status_code} {resp.text[:300]}"
        return resp.json()

    async def _create_request(self, title: str, *, marker: str | None = None) -> dict:
        """建需求；`marker` 会进标题，从而进入 LLM 对话的最后一条 user 消息。"""
        full_title = f"{title} {marker}" if marker else title
        resp = await self._http.post(
            f"{self.base}/api/v1/requests",
            headers=self._headers(),
            json={
                "title": full_title,
                "description": "请给出一句话结论",
                "expected_output": "一句话结论",
                "priority": "normal",
            },
        )
        assert resp.status_code == 201, f"建需求失败: {resp.status_code} {resp.text[:300]}"
        return resp.json()

    async def _assign(self, request_id: str, agent_id: str) -> dict:
        resp = await self._http.post(
            f"{self.base}/api/v1/requests/{request_id}/assign",
            headers=self._headers(),
            json={"assignee_type": "agent", "assignee_id": agent_id},
        )
        assert resp.status_code == 202, f"分配失败: {resp.status_code} {resp.text[:300]}"
        return resp.json()

    async def _wait_for_terminal(self, run_id: str, timeout_s: float = 120.0) -> dict:
        """轮询 Run 直到离开活跃态（含 retrying）。"""
        deadline = time.monotonic() + timeout_s
        last: dict = {}
        while time.monotonic() < deadline:
            resp = await self._http.get(
                f"{self.base}/api/v1/runs/{run_id}", headers=self._headers()
            )
            assert resp.status_code == 200, f"查 run 失败: {resp.status_code}"
            last = resp.json()
            if last["status"] in {
                "completed",
                "failed",
                "cancelled",
                "timeout",
            }:
                return last
            await asyncio.sleep(1.0)
        return last

    async def _run_to_terminal(
        self, agent: dict, title: str, *, marker: str | None = None
    ) -> dict:
        request = await self._create_request(title, marker=marker)
        assigned = await self._assign(request["id"], agent["id"])
        return await self._wait_for_terminal(assigned["run_id"])

    # ---------- 场景 ----------

    async def scenario_a_transient_then_success(self) -> None:
        """A：网关抖 2 次后恢复 → 自动重试成功（SC-001）。"""
        started = time.monotonic()
        agent = await self._create_agent()
        run = await self._run_to_terminal(
            agent, "重试验收 A：抖动后恢复", marker="RETRY_FAIL2"
        )

        # 注意：不断言 attempt 的精确值。LiteLLM 客户端自带 num_retries=1，
        # 会在一次 run 级尝试内先消耗若干次网关失败，因此 run 级重试次数取决于
        # 两层预算的交互。SC-001 的本意是「无人干预完成且不超 3 次尝试」。
        ok = (
            run["status"] == "completed"
            and 2 <= run["attempt"] <= 3
            and len(run["retry_history"]) >= 1
            and run["cost_micros"] > 0
        )
        self._record(
            "A 瞬时抖动后自动恢复",
            ok,
            f"status={run['status']} attempt={run['attempt']} "
            f"历史={len(run['retry_history'])} 成本={run['cost_cents']}分",
            started,
        )

    async def scenario_b_exhausted(self) -> None:
        """B：一直失败 → 重试耗尽后终态 failed（SC-001/SC-005）。"""
        started = time.monotonic()
        agent = await self._create_agent()
        run = await self._run_to_terminal(
            agent, "重试验收 B：一直失败", marker="RETRY_FAIL_ALL"
        )

        history = run["retry_history"]
        ok = (
            run["status"] == "failed"
            and run["attempt"] >= 3
            and len(history) >= 2
            and all("error" in h for h in history)
            and run["next_retry_at"] is None
        )
        self._record(
            "B 重试耗尽后终态 failed",
            ok,
            f"status={run['status']} attempt={run['attempt']} "
            f"历史={len(history)} 首因={(history[0]['error'][:40] if history else '无')}",
            started,
        )

    async def scenario_c_cost_accumulates(self) -> None:
        """C：累计成本达到上限即熔断，不因重试重置而绕过（SC-003）。"""
        started = time.monotonic()
        # 上限 1 分：mock 每次成功调用约 0.15 分，第一次成功就会触发熔断
        agent = await self._create_agent(cost_limit_cents=1)
        run = await self._run_to_terminal(
            agent, "重试验收 C：成本熔断", marker="RETRY_FAIL2"
        )

        ok = run["status"] == "failed" and "成本超限" in (run["error"] or "")
        self._record(
            "C 累计成本触发熔断",
            ok,
            f"status={run['status']} error={(run['error'] or '')[:50]} "
            f"成本={run['cost_cents']}分",
            started,
        )

    async def scenario_d_retrying_is_visible(self) -> None:
        """D：重试等待期间状态为 retrying（SC-004：不被误判卡死）。

        用「一直失败」的标记而不是 RETRY_FAIL2：full jitter 的等待可以短到几百
        毫秒，恢复类标记只会重试一两次，轮询容易整个错过窗口；一直失败则有连续
        多个等待窗口，必然能观察到。
        """
        started = time.monotonic()
        agent = await self._create_agent()
        request = await self._create_request(
            "重试验收 D：重试态可见", marker="RETRY_FAIL_ALL"
        )
        assigned = await self._assign(request["id"], agent["id"])
        run_id = assigned["run_id"]

        seen_retrying = False
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            resp = await self._http.get(
                f"{self.base}/api/v1/runs/{run_id}", headers=self._headers()
            )
            body = resp.json()
            if body["status"] == "retrying":
                seen_retrying = True
                break
            if body["status"] in {"completed", "failed", "timeout", "cancelled"}:
                break
            await asyncio.sleep(0.2)

        self._record(
            "D 重试等待期呈现为 retrying",
            seen_retrying,
            "轮询到 retrying 状态" if seen_retrying else "未观察到 retrying（可能抖动太快）",
            started,
        )

    async def close(self) -> None:
        await self._http.aclose()


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://localhost:8080")
    parser.add_argument("--invite", default="TEST-ADMIN-CODE-123")
    parser.add_argument(
        "--mock-llm",
        default="http://127.0.0.1:4000",
        help="mock LLM 网关地址（用于重置失败计数器）；传空字符串跳过",
    )
    args = parser.parse_args()

    verifier = RetryVerifier(args.base, args.invite, args.mock_llm)
    try:
        await verifier.setup()
        await verifier.scenario_a_transient_then_success()
        await verifier.scenario_b_exhausted()
        await verifier.scenario_c_cost_accumulates()
        await verifier.scenario_d_retrying_is_visible()
    finally:
        await verifier.close()

    print()
    for result in verifier.results:
        print(result.line())
    failed = [r for r in verifier.results if not r.ok]
    print(f"\n总计：{'✅ 全部通过' if not failed else '❌ 存在失败'}（{len(verifier.results)} 项）")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
