"""组策略验证脚本：pipeline（顺序串联）/ router（选择成员）。

针对真实 docker compose 栈验证两条链路：
- pipeline：父 Run 收敛为 completed，stages 按 sequence 排列，各阶段子 Run 均 completed
- router：父 Run 自己执行并记录 routing 决策（选中的成员、依据、路由成本）

注意：mock LLM 网关不实现「只回编号」的路由语义，因此 router 很可能走到
降级分支（取第一个成员）——脚本断言的是「决策被记录且链路跑通」，
选择准确性由单测（test_group_router.py）覆盖。

用法：python strategy_scenarios.py --base http://localhost:8080 --invite TEST-ADMIN-CODE-123
"""

import argparse
import asyncio
import time
import uuid
from dataclasses import dataclass
from typing import Any

import httpx


@dataclass
class Result:
    name: str
    ok: bool
    detail: str = ""
    elapsed_s: float = 0.0

    def line(self) -> str:
        mark = "✅" if self.ok else "❌"
        cost = f"（{self.elapsed_s:.2f}s）" if self.elapsed_s else ""
        return f"{mark} {self.name} — {self.detail}{cost}"


class StrategyVerifier:
    def __init__(self, base: str, invite: str, model: str = "Deepseek-V4.1-Flash") -> None:
        self.base = base.rstrip("/")
        self.invite = invite
        # 模型必须与所打栈的 LLM 端点匹配（默认取当前部署的 LLM_MODEL）；
        # 用 mock 网关时传 gpt-4o-mini。写错的症状是 Run 秒失败
        # No available channel for model ...（006 换端点时踩过同款坑）。
        self.model = model
        self.token: str | None = None
        self.results: list[Result] = []
        self._http = httpx.AsyncClient(timeout=30.0)

    # ---------- 基础 ----------

    async def setup(self) -> None:
        email = f"strategy-{uuid.uuid4().hex[:8]}@test.com"
        resp = await self._http.post(
            f"{self.base}/api/v1/auth/register",
            json={"email": email, "password": "TestPass123!", "invite_code": self.invite},
        )
        assert resp.status_code == 201, f"注册失败: {resp.status_code} {resp.text[:300]}"
        self.token = resp.json()["tokens"]["access"]

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    async def _create_agent(self, name: str, description: str) -> dict[str, Any]:
        resp = await self._http.post(
            f"{self.base}/api/v1/agents",
            headers=self._headers(),
            json={
                "name": name,
                "description": description,
                "system_prompt": f"你是{name}。{description}",
                "model": self.model,
                "max_steps": 6,
                "cost_limit_cents": 10000,
                "tools": ["web_search"],
            },
        )
        assert resp.status_code == 201, f"建 agent 失败: {resp.status_code} {resp.text[:300]}"
        return resp.json()

    async def _create_group(self, strategy: str) -> dict[str, Any]:
        resp = await self._http.post(
            f"{self.base}/api/v1/groups",
            headers=self._headers(),
            json={"name": f"{strategy}-组-{uuid.uuid4().hex[:5]}", "routing_strategy": strategy},
        )
        assert resp.status_code == 201, f"建组失败: {resp.status_code} {resp.text[:300]}"
        return resp.json()

    async def _add_member(self, group_id: str, agent_id: str, position: int) -> None:
        resp = await self._http.post(
            f"{self.base}/api/v1/groups/{group_id}/members",
            headers=self._headers(),
            json={"agent_id": agent_id, "position": position},
        )
        assert resp.status_code == 201, f"加成员失败: {resp.status_code} {resp.text[:300]}"

    async def _create_request(self, title: str) -> dict[str, Any]:
        resp = await self._http.post(
            f"{self.base}/api/v1/requests",
            headers=self._headers(),
            json={
                "title": title,
                "description": "请给出三点结论",
                "expected_output": "三点结论",
                "priority": "normal",
            },
        )
        assert resp.status_code == 201, f"建需求失败: {resp.status_code} {resp.text[:300]}"
        return resp.json()

    async def _assign_group(self, request_id: str, group_id: str) -> dict[str, Any]:
        resp = await self._http.post(
            f"{self.base}/api/v1/requests/{request_id}/assign",
            headers=self._headers(),
            json={"assignee_type": "group", "assignee_id": group_id},
        )
        assert resp.status_code == 202, f"分配失败: {resp.status_code} {resp.text[:300]}"
        return resp.json()

    async def _run_detail(self, run_id: str) -> dict[str, Any]:
        resp = await self._http.get(
            f"{self.base}/api/v1/runs/{run_id}", headers=self._headers()
        )
        assert resp.status_code == 200, f"查 run 失败: {resp.status_code}"
        return resp.json()

    async def _wait_terminal(self, run_id: str, timeout: float = 90.0) -> dict[str, Any]:
        start = time.monotonic()
        while time.monotonic() - start < timeout:
            detail = await self._run_detail(run_id)
            if detail["status"] in {"completed", "failed", "cancelled", "timeout", "needs_input"}:
                return detail
            await asyncio.sleep(0.3)
        raise TimeoutError(f"run {run_id} 超时未达终态（{timeout}s）")

    # ---------- 场景 ----------

    async def pipeline_scenario(self) -> None:
        """pipeline：两阶段顺序执行，父 Run 收敛并记录 stages。"""
        start = time.monotonic()
        writer = await self._create_agent("写手", "负责撰写初稿")
        editor = await self._create_agent("编辑", "负责润色初稿")
        group = await self._create_group("pipeline")
        await self._add_member(group["id"], writer["id"], position=1)
        await self._add_member(group["id"], editor["id"], position=2)
        request = await self._create_request(f"pipeline-{uuid.uuid4().hex[:5]}")

        assigned = await self._assign_group(request["id"], group["id"])
        parent = await self._wait_terminal(assigned["run_id"])
        elapsed = time.monotonic() - start

        ok = (
            parent["status"] == "completed"
            and isinstance(parent.get("result"), dict)
            and parent["result"].get("strategy") == "pipeline"
            and len(parent["result"].get("stages") or []) == 2
        )
        detail = (
            f"父 Run={parent['status']} stages={[s['sequence'] for s in parent['result'].get('stages', [])]} "
            f"各阶段状态={[s['status'] for s in parent['result'].get('stages', [])]}"
            if isinstance(parent.get("result"), dict)
            else f"父 Run={parent['status']} result={parent.get('result')}"
        )
        self.results.append(Result("pipeline 顺序串联 + 父 Run 收敛", ok, detail, elapsed))

        if ok:
            # 各阶段子 Run 都应 completed 且有步骤
            stage_ids = [s["run_id"] for s in parent["result"]["stages"]]
            stage_details = [await self._run_detail(rid) for rid in stage_ids]
            all_done = all(d["status"] == "completed" and d["steps"] for d in stage_details)
            self.results.append(
                Result(
                    "pipeline 各阶段独立完成并留痕",
                    all_done,
                    f"阶段步骤数={[len(d['steps']) for d in stage_details]}",
                )
            )

    async def router_scenario(self) -> None:
        """router：父 Run 自执行并记录路由决策。"""
        start = time.monotonic()
        analyst = await self._create_agent("分析师", "擅长数据分析")
        lawyer = await self._create_agent("法务", "擅长合同与合规")
        group = await self._create_group("router")
        await self._add_member(group["id"], analyst["id"], position=1)
        await self._add_member(group["id"], lawyer["id"], position=2)
        request = await self._create_request(f"router-{uuid.uuid4().hex[:5]}")

        assigned = await self._assign_group(request["id"], group["id"])
        run = await self._wait_terminal(assigned["run_id"])
        elapsed = time.monotonic() - start

        routing = (run.get("result") or {}).get("routing") if isinstance(run.get("result"), dict) else None
        ok = run["status"] == "completed" and isinstance(routing, dict) and routing.get("selected_agent_name")
        detail = (
            f"选中={routing.get('selected_agent_name')} 依据={routing.get('reason')} "
            f"路由成本={routing.get('cost_cents')}分"
            if isinstance(routing, dict)
            else f"status={run['status']} result={run.get('result')}"
        )
        self.results.append(Result("router 选择成员并记录决策", ok, detail, elapsed))

    async def close(self) -> None:
        await self._http.aclose()


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://localhost:8080")
    parser.add_argument("--invite", default="TEST-ADMIN-CODE-123")
    parser.add_argument(
        "--model",
        default="Deepseek-V4.1-Flash",
        help="验收 Agent 使用的模型（必须与所打栈的 LLM 端点匹配）",
    )
    args = parser.parse_args()

    verifier = StrategyVerifier(args.base, args.invite, model=args.model)
    try:
        await verifier.setup()
        await verifier.pipeline_scenario()
        await verifier.router_scenario()
    finally:
        await verifier.close()

    print()
    for result in verifier.results:
        print(result.line())
    failed = [r for r in verifier.results if not r.ok]
    print(f"\n总计：{'✅ 全部通过' if not failed else '❌ 存在失败'}（{len(verifier.results)} 项）")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys_exit = asyncio.run(main())
    raise SystemExit(sys_exit)
