"""External MCP executor contract tests; network and state are injected."""

import pytest

from app.executors.external_tool import ExternalToolExecutionError, ExternalToolExecutor


class FakeClient:
    def __init__(self, result=None, *, error=None):
        self.result = result or {"content": [{"type": "text", "text": "ok"}]}
        self.error = error
        self.initialized = 0
        self.calls = []
        self.cancelled = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def initialize(self):
        self.initialized += 1

    async def call_tool(self, name, arguments, *, timeout=None):
        self.calls.append((name, arguments, timeout))
        if self.error:
            raise self.error
        return self.result

    async def cancel(self, request_id):
        self.cancelled.append(request_id)


def make_executor(client, **overrides):
    config = {
        "stable_name": "mcp_fixture_lookup_ab12",
        "external_name": "lookup",
        "description": "Look up records",
        "endpoint": "https://mcp.example.test/rpc",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
        "permission_snapshot": ["records.read"],
        "required_permissions": ["records.read"],
        "headers": {},
        "client_factory": lambda *_args, **_kwargs: client,
        "max_result_chars": 100,
    }
    config.update(overrides)
    return ExternalToolExecutor(**config)


@pytest.mark.asyncio
async def test_external_executor_exposes_stable_name_and_calls_remote_once():
    client = FakeClient()
    executor = make_executor(client)

    assert executor.name == "mcp_fixture_lookup_ab12"
    assert executor.schema()["function"]["name"] == executor.name
    result = await executor.execute({"query": "hello"})

    assert client.initialized == 1
    assert len(client.calls) == 1
    assert client.calls[0][:2] == ("lookup", {"query": "hello"})
    assert 0 < client.calls[0][2] <= executor.timeout_seconds
    assert result["content"][0]["text"] == "ok"


@pytest.mark.asyncio
async def test_external_executor_rejects_invalid_or_ungranted_arguments_before_network():
    client = FakeClient()
    executor = make_executor(client)

    with pytest.raises(ExternalToolExecutionError) as exc:
        await executor.execute({"query": 42})

    assert exc.value.category == "parameter_error"
    assert client.calls == []
    assert client.initialized == 0


@pytest.mark.asyncio
async def test_external_executor_fails_closed_when_snapshot_lacks_declared_permission():
    client = FakeClient()
    executor = make_executor(client, permission_snapshot=[])

    with pytest.raises(ExternalToolExecutionError) as exc:
        await executor.execute({"query": "hello"})

    assert exc.value.category == "policy_denied"
    assert client.calls == []


@pytest.mark.asyncio
async def test_external_executor_bounds_result_and_classifies_auth_errors_without_retry():
    from app.executors.mcp_client import MCPAuthError

    client = FakeClient(error=MCPAuthError("do not expose remote details"))
    executor = make_executor(client)

    with pytest.raises(ExternalToolExecutionError) as exc:
        await executor.execute({"query": "hello"})

    assert exc.value.category == "auth_error"
    assert "do not expose" not in str(exc.value)
    assert len(client.calls) == 1

    large = FakeClient({"content": [{"type": "text", "text": "x" * 200}]})
    truncated = await make_executor(large, max_result_chars=20).execute({"query": "hello"})
    assert truncated["result_truncated"] is True
    assert len(truncated["content"][0]["text"]) <= 20


@pytest.mark.asyncio
async def test_external_executor_cancels_remote_call_and_discards_late_result():
    import asyncio

    started = asyncio.Event()

    class SlowClient(FakeClient):
        async def call_tool(self, name, arguments, *, timeout=None):
            self.calls.append((name, arguments, timeout))
            started.set()
            await asyncio.sleep(1)
            return {"content": [{"type": "text", "text": "late secret"}]}

    client = SlowClient()
    checks = 0

    async def abort_check():
        nonlocal checks
        checks += 1
        return checks >= 2

    with pytest.raises(ExternalToolExecutionError) as exc:
        await make_executor(client, abort_check=abort_check).execute({"query": "hello"})

    assert exc.value.category == "cancelled"
    assert client.cancelled
    assert started.is_set()


@pytest.mark.asyncio
async def test_external_executor_maps_transport_tool_and_timeout_categories():
    import httpx

    from app.executors.mcp_client import MCPToolError, MCPTransportError

    for error, category in (
        (MCPTransportError("untrusted details"), "transport_error"),
        (MCPToolError("untrusted details"), "tool_error"),
        (TimeoutError(), "timeout"),
        (httpx.TimeoutException("untrusted details"), "timeout"),
    ):
        client = FakeClient(error=error)
        with pytest.raises(ExternalToolExecutionError) as exc:
            await make_executor(client).execute({"query": "hello"})
        assert exc.value.category == category
        assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_a_cancelled_task_stays_cancelled_and_its_call_is_stopped():
    """任务被真正取消时，CancelledError 必须继续抛出，且不带走在跑的 call。

    把 CancelledError 改写成业务错误，会让调用方在正在关闭的 loop 上继续写库；
    而把 create_task 丢在异常路径上，远端 tools/call 会在客户端关闭后继续跑。
    """
    import asyncio

    started = asyncio.Event()
    observed: dict[str, bool] = {}

    class HangingClient(FakeClient):
        async def call_tool(self, name, arguments, *, timeout=None):
            self.calls.append((name, arguments, timeout))
            started.set()
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                observed["cancelled"] = True
                raise
            return {"content": []}

    records: list[tuple[str, str | None]] = []

    async def recorder(**kwargs):
        records.append((kwargs["status"], kwargs.get("error_category")))

    executor = make_executor(HangingClient(), invocation_recorder=recorder)
    task = asyncio.create_task(executor.execute({"query": "hello"}))
    await asyncio.wait_for(started.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # 远端调用被取消（不是留在后台继续等），并且留下了一条 cancelled 记录
    assert observed.get("cancelled") is True
    assert ("cancelled", "cancelled") in records


@pytest.mark.asyncio
async def test_abort_check_failure_does_not_leave_the_call_running():
    """abort_check 自己抛错（例如 DB 抖动）时，也不能把 call 任务留在后台。"""
    import asyncio

    started = asyncio.Event()
    observed: dict[str, bool] = {}

    class HangingClient(FakeClient):
        async def call_tool(self, name, arguments, *, timeout=None):
            self.calls.append((name, arguments, timeout))
            started.set()
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                observed["cancelled"] = True
                raise
            return {"content": []}

    checks = 0

    async def abort_check():
        nonlocal checks
        checks += 1
        if checks >= 2:
            raise RuntimeError("database is gone")
        return False

    with pytest.raises(RuntimeError):
        await make_executor(HangingClient(), abort_check=abort_check).execute({"query": "hello"})

    assert started.is_set()
    assert observed.get("cancelled") is True
