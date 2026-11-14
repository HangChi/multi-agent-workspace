"""越权工具调用的结构化拒绝与阈值熔断。"""

import pytest

from app.core.config import settings
from app.orchestrator.runner import ReActRunner
from tests.unit.test_runner import (
    FakeExecutor,
    FakeLLM,
    FakeLLMResponse,
    FakeRun,
    FakeToolCall,
    Sink,
)


@pytest.mark.asyncio
async def test_catalog_tool_denied_has_available_tools_and_event() -> None:
    llm = FakeLLM(
        [
            FakeLLMResponse(tool_calls=[FakeToolCall("run_shell", {"command": "id"})]),
            FakeLLMResponse(content="不能执行命令，我改用计算。"),
        ]
    )
    sink = Sink()
    runner = ReActRunner(llm=llm, executors={"calculate": FakeExecutor()})
    run = FakeRun()
    status = await runner.run(
        run=run,
        request_text="test",
        save_step=sink.save_step,
        publish_event=sink.publish,
        set_status=sink.set_status,
        set_cost=sink.set_cost,
    )
    assert status == "completed"
    assert sink.steps[1]["output"]["denied"] is True
    assert "calculate" in sink.steps[1]["output"]["message"]
    assert any(event["type"] == "tool_denied" for event in sink.events)


@pytest.mark.asyncio
async def test_repeated_denials_abort_at_threshold(monkeypatch) -> None:
    monkeypatch.setattr(settings, "tool_deny_threshold", 2)
    llm = FakeLLM(
        [
            FakeLLMResponse(tool_calls=[FakeToolCall("run_shell", {})]),
            FakeLLMResponse(tool_calls=[FakeToolCall("run_shell", {})]),
            FakeLLMResponse(content="不应到达"),
        ]
    )
    sink = Sink()
    runner = ReActRunner(llm=llm, executors={})
    run = FakeRun()
    status = await runner.run(
        run=run,
        request_text="test",
        save_step=sink.save_step,
        publish_event=sink.publish,
        set_status=sink.set_status,
        set_cost=sink.set_cost,
    )
    assert status == "failed"
    assert run.status == "failed"
    assert len(llm.calls) == 2
    assert "越权" in (run.error or "")
