"""Run 终结器的行为快照（R4）。

`runner.run()` 原先把「transition + set_status + publish_event」这段三连复制了
7 次。抽成 `_settle()` 之后，风险从「重复」变成「抽取时顺手统一了本来不同的
东西」——所以这里逐条把**每一处终结分支的对外表现**钉住：

- 终态是什么（`run.status`）
- 事件类型是什么（前端按类型分支渲染，不能变）
- 事件 payload 里有哪些字段（`error` / `message` / `result`）

这不是「测实现细节」：这三个字段就是 WS 契约，改任何一个前端都要跟着改。
本文件的价值在于——将来谁觉得「同样是取消，两处事件类型该统一一下」，
会在这里看到那不是笔误而是**已声明的差异**，要么一起改前端，要么别动。

刻意把「取消」的两处差异列出来（`status_change` vs `run_cancelled`）：
它们确实不一致，但统一属于行为变更（R4 是重构，不改行为），记在这里待产品决定。
"""

from typing import Any

import pytest

from app.orchestrator.runner import (
    CostLimitExceeded,
    ExecutionTimeout,
    MaxStepsExceeded,
    NeedsInput,
    ReActRunner,
)
from app.orchestrator.state_machine import RunStatus

from .test_runner import FakeLLM, FakeLLMResponse, FakeRun, Sink, _make_runner


def _terminal_events(sink: Sink) -> list[dict[str, Any]]:
    """除 status_change(running) 之外的全部事件。"""
    return [e for e in sink.events if e["type"] != "status_change"]


async def _run(
    llm: FakeLLM, *, expect: type[BaseException] | None = None, **kwargs: Any
) -> tuple[FakeRun, Sink]:
    runner, run, sink = _make_runner(llm, **kwargs)
    if expect is None:
        await runner.run(
            run=run,
            request_text="x",
            save_step=sink.save_step,
            publish_event=sink.publish,
            set_status=sink.set_status,
            set_cost=sink.set_cost,
        )
    else:
        with pytest.raises(expect):
            await runner.run(
                run=run,
                request_text="x",
                save_step=sink.save_step,
                publish_event=sink.publish,
                set_status=sink.set_status,
                set_cost=sink.set_cost,
            )
    return run, sink


async def test_completed_emits_run_completed_with_result() -> None:
    run, sink = await _run(FakeLLM([FakeLLMResponse(content="答案")]))
    assert run.status == RunStatus.COMPLETED.value

    terminal = [e for e in sink.events if e["type"] == "run_completed"]
    assert len(terminal) == 1
    assert terminal[0] == {"type": "run_completed", "result": {"answer": "答案"}}


async def test_max_steps_emits_run_failed_with_error_text() -> None:
    """max_steps 超限：状态 failed，事件 `run_failed`，payload 带 error 原文。"""
    run, sink = await _run(FakeLLM([]), expect=MaxStepsExceeded, max_steps=0)

    assert run.status == RunStatus.FAILED.value
    failed = [e for e in sink.events if e["type"] == "run_failed"]
    assert len(failed) == 1
    assert failed[0]["error"] == "已超过最大步骤数（0），执行终止"
    # 事件里的文案必须与 run.error 是同一份，否则前端与详情页对不上
    assert failed[0]["error"] == run.error


async def test_timeout_emits_run_timeout_without_error_field() -> None:
    """超时：事件 `run_timeout`，**不带 error 字段**（前端靠类型判断，不读文案）。"""
    run, sink = await _run(FakeLLM([]), expect=ExecutionTimeout, timeout_seconds=-1)

    assert run.status == RunStatus.TIMEOUT.value
    timeout = [e for e in sink.events if e["type"] == "run_timeout"]
    assert timeout == [{"type": "run_timeout"}]


async def test_llm_exception_emits_run_failed_and_returns_instead_of_raising() -> None:
    """LLM 异常是「正常返回」而非上抛：run 已 failed，调用方不必再捕获。"""

    class BoomLLM:
        async def chat(self, messages, **kwargs):
            raise RuntimeError("模型端点 500")

    runner, run, sink = _make_runner(BoomLLM())  # type: ignore[arg-type]
    status = await runner.run(
        run=run,
        request_text="x",
        save_step=sink.save_step,
        publish_event=sink.publish,
        set_status=sink.set_status,
        set_cost=sink.set_cost,
    )

    assert status == RunStatus.FAILED.value
    failed = [e for e in sink.events if e["type"] == "run_failed"]
    assert failed[0]["error"] == "LLM 调用失败: 模型端点 500"
    assert failed[0]["error"] == run.error


async def test_cost_limit_emits_run_failed_with_breaker_message() -> None:
    """成本熔断：文案里必须同时出现实际花费与上限，便于用户判断是否要调整。"""
    run, sink = await _run(
        FakeLLM([FakeLLMResponse(content="x", cost_cents=10)]),
        expect=CostLimitExceeded,
        cost_limit_cents=1,
    )

    assert run.status == RunStatus.FAILED.value
    failed = [e for e in sink.events if e["type"] == "run_failed"]
    assert failed[0]["error"] == run.error
    assert "10 分" in failed[0]["error"] and "上限 1 分" in failed[0]["error"]


async def test_needs_input_emits_message_field() -> None:
    """挂起：事件 `needs_input` 带 `message`（前端把它显示成追问）。"""
    run, sink = await _run(
        FakeLLM(
            [FakeLLMResponse(needs_input=True, needs_input_message="要哪个季度？")]
        ),
        expect=NeedsInput,
    )

    assert run.status == RunStatus.NEEDS_INPUT.value
    asked = [e for e in sink.events if e["type"] == "needs_input"]
    assert asked == [{"type": "needs_input", "message": "要哪个季度？"}]


async def test_cancel_check_emits_status_change_not_run_cancelled() -> None:
    """循环顶部的取消检查发的是 `status_change`——**与工具段的 `run_cancelled` 不同**。

    这个差异是既存行为，R4 只做抽取、不做统一（统一要同时改前端）。
    这里把它写成断言，是为了让「不一致」变成已知且有据可查，而不是靠读代码发现。
    """

    async def always_abort() -> bool:
        return True

    runner, run, sink = _make_runner(FakeLLM([]))
    status = await runner.run(
        run=run,
        request_text="x",
        save_step=sink.save_step,
        publish_event=sink.publish,
        set_status=sink.set_status,
        set_cost=sink.set_cost,
        should_abort=always_abort,
    )

    assert status == "cancelled"
    assert run.status == RunStatus.CANCELLED.value
    types = [e["type"] for e in sink.events]
    assert types == ["status_change", "status_change"]
    assert "run_cancelled" not in types
    # 第二个 status_change 带的是取消后的状态
    assert sink.events[-1]["status"] == RunStatus.CANCELLED.value


async def test_error_is_visible_at_publish_time() -> None:
    """终结器必须先把 error 写进 run、再发事件。

    顺序反了的话，事件里的 error 会是 None（前端拿到空错误），或者调用方
    读 `run.error` 时还是旧值。用 max_steps=0 走确定的失败路径。
    """
    runner, run, sink = _make_runner(FakeLLM([]), max_steps=0)

    seen: list[str | None] = []

    async def watch(event: dict[str, Any]) -> None:
        if event["type"] == "run_failed":
            seen.append(run.error)
        sink.events.append(event)

    with pytest.raises(MaxStepsExceeded):
        await runner.run(
            run=run,
            request_text="x",
            save_step=sink.save_step,
            publish_event=watch,
            set_status=sink.set_status,
            set_cost=sink.set_cost,
        )

    assert seen == ["已超过最大步骤数（0），执行终止"]


def test_settle_is_not_a_module_level_helper() -> None:
    """`_settle` 刻意做成 `run()` 的内联闭包，不上提为模块函数。

    它捕获 `run` / `set_status` / `publish_event` 三个局部量；提成模块函数就得
    把三个都当参数传，调用点从「一句话」变成「一句话 + 三个参数」——重复没消掉，
    只是从 7 段变成 7 行长参数调用。它只服务于这一个循环。
    """
    assert not hasattr(ReActRunner, "_settle")
