"""ReAct behavior for external tool failures and late terminal state."""

from dataclasses import dataclass, field
from typing import Any

import pytest

from app.executors.external_tool import ExternalToolExecutionError
from app.orchestrator.runner import ReActRunner
from app.orchestrator.state_machine import RunStatus


@dataclass
class ToolCall:
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class Response:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    cost_cents: int = 0
    needs_input: bool = False
    needs_input_message: str = ""


class LLM:
    def __init__(self):
        self.calls = 0

    async def chat(self, _messages, **_kwargs):
        self.calls += 1
        return Response(tool_calls=[ToolCall("mcp_fixture_lookup_ab12", {"query": "x"})])


class ExternalFailureExecutor:
    name = "mcp_fixture_lookup_ab12"
    is_external_tool = True
    version = "1.0.0"

    def schema(self):
        return {"type": "function", "function": {"name": self.name, "parameters": {"type": "object"}}}

    async def execute(self, _arguments):
        raise ExternalToolExecutionError("auth_error", "safe auth failure")


@dataclass
class Run:
    id: str = "run-external-1"
    status: str = "queued"
    current_step: int = 0
    cost_cents: int = 0
    error: str | None = None
    result: dict | None = None
    started_at: Any = None
    finished_at: Any = None

    def mark_started(self):
        pass

    def mark_finished(self):
        pass

    def clear_execution(self):
        pass


@pytest.mark.asyncio
async def test_external_tool_failure_fails_run_without_llm_success_or_raw_error():
    llm = LLM()
    runner = ReActRunner(llm=llm, executors={"mcp_fixture_lookup_ab12": ExternalFailureExecutor()})
    run = Run()
    steps, events = [], []

    async def capture_step(step):
        steps.append(step)

    async def capture_event(event):
        events.append(event)

    async def noop(_value):
        pass

    status = await runner.run(
        run=run,
        request_text="lookup",
        save_step=capture_step,
        publish_event=capture_event,
        set_status=noop,
        set_cost=noop,
    )

    assert status == RunStatus.FAILED.value
    assert run.status == RunStatus.FAILED.value
    assert llm.calls == 1
    tool_step = next(step for step in steps if step["kind"] == "tool")
    assert tool_step["status"] == "failed"
    assert tool_step["output"]["error_category"] == "auth_error"
    assert tool_step["input"] == {"argument_keys": ["query"]}
    assert tool_step["id"]
    assert "safe auth failure" not in str(tool_step)
    assert any(event["type"] == "external_tool_call_started" for event in events)
    assert any(event["type"] == "external_tool_call_update" for event in events)


class ExternalSuccessExecutor(ExternalFailureExecutor):
    async def execute(self, _arguments):
        return {"content": [{"type": "text", "text": "sensitive remote output"}], "result_truncated": False}


@pytest.mark.asyncio
async def test_external_tool_raw_arguments_and_results_are_not_persisted_or_emitted():
    class SuccessLLM(LLM):
        def __init__(self):
            super().__init__()
            self.messages = []

        async def chat(self, _messages, **_kwargs):
            self.messages.append(_messages)
            self.calls += 1
            if self.calls == 1:
                return Response(tool_calls=[ToolCall("mcp_fixture_lookup_ab12", {"query": "private query"})])
            return Response(content="done")

    llm = SuccessLLM()
    runner = ReActRunner(llm=llm, executors={"mcp_fixture_lookup_ab12": ExternalSuccessExecutor()})
    run = Run()
    steps, events = [], []

    async def capture_step(step):
        steps.append(step)

    async def capture_event(event):
        events.append(event)

    async def noop(_value):
        pass

    await runner.run(
        run=run,
        request_text="lookup",
        save_step=capture_step,
        publish_event=capture_event,
        set_status=noop,
        set_cost=noop,
    )
    external_step = next(step for step in steps if step["kind"] == "tool")
    assert external_step["input"] == {"argument_keys": ["query"]}
    assert external_step["output"]["status"] == "succeeded"
    assert "private query" not in str(steps)
    assert "sensitive remote output" not in str(steps)
    assert "private query" not in str(events)
    assert "sensitive remote output" not in str(events)
    assert "sensitive remote output" in str(llm.messages[1])  # Model context may receive bounded output.


class ExternalCancelledExecutor(ExternalFailureExecutor):
    async def execute(self, _arguments):
        raise ExternalToolExecutionError("cancelled", "safe cancellation")


@pytest.mark.asyncio
async def test_external_cancellation_terminates_run_as_cancelled_not_completed():
    runner = ReActRunner(
        llm=LLM(),
        executors={"mcp_fixture_lookup_ab12": ExternalCancelledExecutor()},
    )
    run = Run()
    events = []

    async def capture_event(event):
        events.append(event)

    async def noop(_value):
        pass

    status = await runner.run(
        run=run,
        request_text="lookup",
        save_step=noop,
        publish_event=capture_event,
        set_status=noop,
        set_cost=noop,
    )

    assert status == RunStatus.CANCELLED.value
    assert run.status == RunStatus.CANCELLED.value
    assert any(event["type"] == "run_cancelled" for event in events)
    assert not any(event["type"] == "run_completed" for event in events)
