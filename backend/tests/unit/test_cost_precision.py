"""成本精度：单次调用的零头必须能累加成整分。

回归点（2026-11-17 审计）：`cost_cents` 是整数分，而一次 gpt-4o-mini 调用约
0.015 分，`round()` 后是 0。于是「10 步小调用」的总成本显示为 0，月配额与
每 Run 熔断都读这个字段，等于系统性放宽。

修复方式：`cost_micros`（1 分 = 1e6 微分）做精确累计，`cost_cents` 由它换算，
只作展示与阈值比较。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from app.executors.llm import usage_to_cost_cents, usage_to_cost_micros
from app.orchestrator.runner import ReActRunner
from app.orchestrator.state_machine import RunStatus

# 1000 prompt + 1000 completion tokens，gpt-4o-mini 单价：
# 0.00015 + 0.0006 = 0.00075 USD = 0.075 分 = 75000 微分
SMALL_USAGE = {"prompt_tokens": 1000, "completion_tokens": 1000}


def test_single_small_call_rounds_to_zero_cents_but_keeps_micros():
    cents = usage_to_cost_cents("gpt-4o-mini", SMALL_USAGE)
    micros = usage_to_cost_micros("gpt-4o-mini", SMALL_USAGE)

    assert cents == 0  # 这就是旧口径丢失的零头
    assert micros == 75_000


def test_accumulated_small_calls_reach_a_cent():
    """旧口径：20 次 × 0 = 0 分；新口径：20 × 75000 微分 = 1.5 分。"""
    old_total = sum(usage_to_cost_cents("gpt-4o-mini", SMALL_USAGE) for _ in range(20))
    new_total = sum(usage_to_cost_micros("gpt-4o-mini", SMALL_USAGE) for _ in range(20))

    assert old_total == 0
    assert new_total == 1_500_000
    assert round(new_total / 1_000_000) == 2


def test_unknown_model_still_priced_conservatively():
    """未知模型按 gpt-4o-mini 估：宁高不低，避免熔断失效。"""
    assert usage_to_cost_micros("mystery-model", SMALL_USAGE) > 0
    assert usage_to_cost_micros("gpt-4o-mini", None) == 0


# ---------- runner 侧累计 ----------


@dataclass
class _ToolCall:
    pass


@dataclass
class _Response:
    content: str = "最终答案: ok"
    tool_calls: list[Any] = field(default_factory=list)
    cost_cents: int = 0
    cost_micros: int = 0
    needs_input: bool = False
    needs_input_message: str = ""


class _LLM:
    def __init__(self, responses: list[_Response]) -> None:
        self.responses = list(responses)

    async def chat(self, messages, **kwargs):
        return self.responses.pop(0)


@dataclass
class _Run:
    id: str = "r1"
    status: str = "queued"
    current_step: int = 0
    cost_cents: int = 0
    cost_micros: int = 0
    error: str | None = None
    result: dict[str, Any] | None = None
    started_at: Any = None
    finished_at: Any = None

    def mark_started(self) -> None:
        self.started_at = "started"

    def mark_finished(self) -> None:
        self.finished_at = "finished"

    def clear_execution(self) -> None:
        self.cost_cents = 0
        self.cost_micros = 0


def _run_once(responses: list[_Response]) -> _Run:
    run = _Run()
    costs: list[int] = []

    async def save_step(step):
        return None

    async def publish(event):
        return None

    async def set_status(status):
        return None

    async def set_cost(cents):
        costs.append(cents)

    runner = ReActRunner(llm=_LLM(responses), executors={}, max_steps=10)
    asyncio.run(
        runner.run(
            run=run,
            request_text="hi",
            save_step=save_step,
            publish_event=publish,
            set_status=set_status,
            set_cost=set_cost,
        )
    )
    run.last_costs = costs  # type: ignore[attr-defined]
    return run


def test_runner_accumulates_micros_across_resumes():
    """两次小调用（挂起 → 回复续跑）各 0.075 分：cents 仍是 0，micros 必须精确累计。"""
    run = _Run()

    async def save_step(step):
        return None

    async def publish(event):
        return None

    async def set_status(status):
        return None

    async def set_cost(cents):
        return None

    async def call(responses: list[_Response]) -> None:
        runner = ReActRunner(llm=_LLM(responses), executors={}, max_steps=10)
        await runner.run(
            run=run,
            request_text="hi",
            save_step=save_step,
            publish_event=publish,
            set_status=set_status,
            set_cost=set_cost,
        )

    # 第一次：需要补充信息（成本已发生并被累计）
    from app.orchestrator.runner import NeedsInput

    try:
        asyncio.run(
            call(
                [
                    _Response(
                        needs_input=True,
                        needs_input_message="请补充",
                        cost_micros=75_000,
                    )
                ]
            )
        )
    except NeedsInput:
        pass
    # 第二次：用户回复后续跑并结束
    asyncio.run(call([_Response(cost_micros=75_000)]))

    assert run.status == RunStatus.COMPLETED.value
    assert run.cost_micros == 150_000
    assert run.cost_cents == 0  # 0.15 分，四舍五入仍是 0


def test_runner_resumes_from_persisted_micros():
    """needs_input 恢复：从已落库的 micros 续算，不能清零。"""
    run = _Run(cost_cents=0, cost_micros=900_000)  # 0.9 分

    async def save_step(step):
        return None

    async def publish(event):
        return None

    async def set_status(status):
        return None

    async def set_cost(cents):
        return None

    runner = ReActRunner(llm=_LLM([_Response(cost_micros=200_000)]), executors={})
    asyncio.run(
        runner.run(
            run=run,
            request_text="hi",
            save_step=save_step,
            publish_event=publish,
            set_status=set_status,
            set_cost=set_cost,
        )
    )

    assert run.cost_micros == 1_100_000  # 0.9 + 0.2 分
    assert run.cost_cents == 1
