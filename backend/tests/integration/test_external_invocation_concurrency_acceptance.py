"""Bounded 20-success/20-failure MCP load acceptance against a local fixture."""

import asyncio
import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from deploy.verify.mcp_fixture_server import handle_message

from app.executors.external_tool import ExternalToolExecutionError, ExternalToolExecutor
from app.executors.mcp_client import MCPClient


@pytest.mark.asyncio
async def test_twenty_success_and_twenty_failure_calls_remain_bounded():
    active = 0
    peak_active = 0
    lock = asyncio.Lock()

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal active, peak_active
        message = json.loads(request.content)
        method = message.get("method")
        if method == "tools/call":
            async with lock:
                active += 1
                peak_active = max(peak_active, active)
            try:
                name = message.get("params", {}).get("name")
                if name == "timeout_search":
                    await asyncio.sleep(0.2)
                else:
                    await asyncio.sleep(0.005)
                scenario = "tool-error" if name == "error_search" else "normal"
                return httpx.Response(
                    200, json=handle_message(message, scenario=scenario)
                )
            finally:
                async with lock:
                    active -= 1
        return httpx.Response(200, json=handle_message(message, scenario="normal"))

    transport = httpx.MockTransport(handler)
    semaphore = asyncio.Semaphore(8)

    async def invoke(index: int) -> str:
        async with semaphore, httpx.AsyncClient(transport=transport) as http:
            executor = ExternalToolExecutor(
                stable_name=f"mcp_load_search_{index}",
                external_name=(
                    "error_search"
                    if 20 <= index < 30
                    else "timeout_search"
                    if index >= 30
                    else "search"
                ),
                description="fixture load test",
                endpoint="https://fixture.example.test/mcp",
                input_schema={
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                },
                permission_snapshot=[],
                required_permissions=[],
                client_factory=lambda *args, **kwargs: MCPClient(
                    *args, http_client=http, **kwargs
                ),
                timeout_seconds=0.05,
            )
            try:
                result = await executor.execute({"query": f"q-{index}"})
                assert result["isError"] is False
                return "succeeded"
            except ExternalToolExecutionError as exc:
                expected = "tool_error" if 20 <= index < 30 else "timeout"
                assert exc.category == expected
                return exc.category

    outcomes = await asyncio.gather(*(invoke(index) for index in range(40)))
    assert outcomes.count("succeeded") == 20
    assert outcomes.count("tool_error") == 10
    assert outcomes.count("timeout") == 10
    assert peak_active <= 8
    assert active == 0
