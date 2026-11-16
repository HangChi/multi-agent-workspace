"""Fixture-backed MCP invocation scenarios for the complete external path."""

import asyncio
import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from deploy.verify.mcp_fixture_server import handle_message

from app.executors.external_tool import ExternalToolExecutionError, ExternalToolExecutor
from app.executors.mcp_client import MCPClient, MCPProtocolError, MCPToolError


def _transport(scenario: str):
    async def handler(request: httpx.Request) -> httpx.Response:
        if scenario == "auth-error":
            return httpx.Response(401)
        if scenario == "transport-error":
            raise httpx.ConnectError("fixture connection failure", request=request)
        message = json.loads(request.content)
        payload = await asyncio.to_thread(handle_message, message, scenario=scenario)
        return httpx.Response(200, json=payload)

    return httpx.MockTransport(handler)


def _executor(http: httpx.AsyncClient, *, scenario: str = "normal", **overrides):
    options = {
        "stable_name": "mcp_fixture_search_a1b2",
        "external_name": "search",
        "description": "fixture",
        "endpoint": "https://fixture.example.test/rpc",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string", "minLength": 1}},
            "required": ["query"],
            "additionalProperties": False,
        },
        "permission_snapshot": [],
        "required_permissions": [],
        "client_factory": lambda *args, **kwargs: MCPClient(
            *args, http_client=http, **kwargs
        ),
    }
    options.update(overrides)
    return ExternalToolExecutor(**options)


@pytest.mark.asyncio
async def test_fixture_server_success_and_protocol_discovery():
    async with httpx.AsyncClient(transport=_transport("normal")) as http, MCPClient("https://fixture.example.test/rpc", http_client=http) as client:
        await client.initialize()
        tools = await client.list_tools()
        assert [tool.name for tool in tools] == ["search", "slow_search", "error_search"]
        result = await client.call_tool("search", {"query": "fixture"})
        assert result["isError"] is False


@pytest.mark.asyncio
async def test_fixture_server_classifies_tool_protocol_and_bound_failures():
    async with httpx.AsyncClient(transport=_transport("tool-error")) as http, MCPClient("https://fixture.example.test/rpc", http_client=http) as client:
        await client.initialize()
        with pytest.raises(MCPToolError):
            await client.call_tool("error_search", {})

    async with httpx.AsyncClient(transport=_transport("malformed")) as http, MCPClient("https://fixture.example.test/rpc", http_client=http) as client:
        await client.initialize()
        with pytest.raises(MCPProtocolError):
            await client.list_tools()

    async with httpx.AsyncClient(transport=_transport("oversized")) as http:
        executor = _executor(
            http,
            client_factory=lambda *args, **kwargs: MCPClient(
                *args, http_client=http, max_response_bytes=400_000, **kwargs
            ),
            max_result_chars=20,
        )
        result = await executor.execute({"query": "fixture"})
        assert result["result_truncated"] is True
        assert len(result["content"][0]["text"]) <= 20

    async with httpx.AsyncClient(transport=_transport("timeout")) as http:
        executor = _executor(
            http,
            external_name="slow_search",
            input_schema={
                "type": "object",
                "properties": {"delay_ms": {"type": "integer", "minimum": 1}},
                "required": ["delay_ms"],
            },
            timeout_seconds=0.01,
        )
        with pytest.raises(ExternalToolExecutionError) as exc:
            await executor.execute({"delay_ms": 100})
        assert exc.value.category == "timeout"


@pytest.mark.asyncio
async def test_fixture_executor_covers_preflight_auth_transport_and_cancellation_categories():
    async with httpx.AsyncClient(transport=_transport("normal")) as http:
        invalid = _executor(http)
        with pytest.raises(ExternalToolExecutionError) as exc:
            await invalid.execute({"query": 42})
        assert exc.value.category == "parameter_error"

        denied = _executor(http, required_permissions=["records.read"])
        with pytest.raises(ExternalToolExecutionError) as exc:
            await denied.execute({"query": "fixture"})
        assert exc.value.category == "policy_denied"

    async with httpx.AsyncClient(transport=_transport("auth-error")) as http:
        with pytest.raises(ExternalToolExecutionError) as exc:
            await _executor(http).execute({"query": "fixture"})
        assert exc.value.category == "auth_error"

    async with httpx.AsyncClient(transport=_transport("transport-error")) as http:
        with pytest.raises(ExternalToolExecutionError) as exc:
            await _executor(http).execute({"query": "fixture"})
        assert exc.value.category == "transport_error"

    async with httpx.AsyncClient(transport=_transport("timeout")) as http:
        calls = 0

        async def abort_check() -> bool:
            nonlocal calls
            calls += 1
            return calls > 2

        executor = _executor(
            http,
            external_name="slow_search",
            input_schema={
                "type": "object",
                "properties": {"delay_ms": {"type": "integer", "minimum": 1}},
                "required": ["delay_ms"],
            },
            timeout_seconds=1,
            abort_check=abort_check,
        )
        with pytest.raises(ExternalToolExecutionError) as exc:
            await executor.execute({"delay_ms": 500})
        assert exc.value.category == "cancelled"


@pytest.mark.asyncio
async def test_fixture_successful_tool_result_continues_the_existing_runner():
    from dataclasses import dataclass, field
    from typing import Any

    from app.orchestrator.runner import ReActRunner

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
            if self.calls == 1:
                return Response(tool_calls=[ToolCall("mcp_fixture_search_a1b2", {"query": "fixture"})])
            return Response(content="fixture result consumed")

    @dataclass
    class Run:
        id: str = "fixture-run"
        status: str = "queued"
        current_step: int = 0
        cost_cents: int = 0
        error: str | None = None
        result: dict | None = None
        started_at: Any = None
        finished_at: Any = None

        def mark_started(self):
            self.started_at = True

        def mark_finished(self):
            self.finished_at = True

        def clear_execution(self):
            return None

    async with httpx.AsyncClient(transport=_transport("normal")) as http:
        executor = _executor(http)
        llm = LLM()
        runner = ReActRunner(llm=llm, executors={executor.name: executor})
        run = Run()

        async def noop(_value):
            return None

        status = await runner.run(
            run=run,
            request_text="use the fixture search",
            save_step=noop,
            publish_event=noop,
            set_status=noop,
            set_cost=noop,
        )

    assert status == "completed"
    assert llm.calls == 2
