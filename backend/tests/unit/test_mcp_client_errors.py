"""MCP 客户端的协议边界单测（015 覆盖率补齐）。

MCP 是外部进程/服务，返回什么都不能假设。这个客户端的价值就在于**把各种
畸形响应翻译成明确的错误类型**，让上层决定「重试、降级还是标记工具不可用」：

- 401/403 → MCPAuthError（凭证问题，重试无用）
- 超时 → 原样抛出（契约把 timeout 与 transport_error 分开，不能糊成一种）
- 响应体超限 → MCPTransportError（防止一个大响应把 worker 内存打爆）
- 非法 JSON / 错误信封 / result 不是对象 → MCPProtocolError

这些分支在真实环境里要「造一个坏服务端」才能触发，用 httpx.MockTransport
注入最省事。
"""

import json

import httpx
import pytest

from app.executors.mcp_client import (
    MCPAuthError,
    MCPClient,
    MCPError,
    MCPProtocolError,
    MCPTransportError,
)


def _client(handler, **kwargs) -> MCPClient:
    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport)
    return MCPClient("https://mcp.test/rpc", http_client=http, **kwargs)


async def test_request_requires_transport():
    """没有注入 client 又没进上下文：必须报错，而不是 NoneType 崩。"""
    client = MCPClient("https://mcp.test/rpc")
    with pytest.raises(MCPTransportError):
        await client._request("tools/list")


async def test_context_manager_owns_and_closes_transport():
    """自建 client 要自己关；注入的 client 不能关（调用方还要用）。"""
    async with MCPClient("https://mcp.test/rpc", call_timeout=1) as client:
        assert client._http is not None
        owned = client._owned_http
        assert owned is not None
    assert client._http is None
    assert client._owned_http is None
    assert owned.is_closed


async def test_request_returns_result_object():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "result": {"tools": []}}
        )

    client = _client(handler)
    assert await client._request("tools/list") == {"tools": []}


async def test_auth_status_maps_to_auth_error():
    client = _client(lambda request: httpx.Response(403))
    with pytest.raises(MCPAuthError):
        await client._request("tools/list")


async def test_declared_content_length_over_limit_rejected():
    """先看 content-length 就拒绝：不必把超大响应读进内存。"""
    client = _client(
        lambda request: httpx.Response(200, headers={"content-length": "999999"}),
        max_response_bytes=1024,
    )
    with pytest.raises(MCPTransportError):
        await client._request("tools/list")


async def test_streamed_body_over_limit_rejected():
    """没有 content-length 的分块响应：边读边判，超了立刻断。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 4096)

    client = _client(handler, max_response_bytes=1024)
    with pytest.raises(MCPTransportError):
        await client._request("tools/list")


async def test_timeout_is_not_laundered_into_transport_error():
    """契约把 timeout 与 transport_error 分开，这里不能吞掉原异常。"""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("too slow", request=request)

    client = _client(handler)
    with pytest.raises(httpx.TimeoutException):
        await client._request("tools/list")


async def test_invalid_json_maps_to_protocol_error():
    client = _client(lambda request: httpx.Response(200, content=b"not json"))
    with pytest.raises(MCPProtocolError):
        await client._request("tools/list")


async def test_bad_envelope_maps_to_protocol_error():
    client = _client(
        lambda request: httpx.Response(200, json={"id": 1, "result": {"ok": True}})
    )
    with pytest.raises(MCPProtocolError):
        await client._request("tools/list")


async def test_error_payload_message_is_surfaced():
    client = _client(
        lambda request: httpx.Response(
            200, json={"jsonrpc": "2.0", "error": {"message": "tool exploded"}}
        )
    )
    with pytest.raises(MCPProtocolError) as exc:
        await client._request("tools/call")
    assert "tool exploded" in str(exc.value)


async def test_non_dict_error_payload_still_protocol_error():
    client = _client(
        lambda request: httpx.Response(200, json={"jsonrpc": "2.0", "error": "boom"})
    )
    with pytest.raises(MCPProtocolError) as exc:
        await client._request("tools/call")
    assert "protocol error" in str(exc.value)


async def test_non_object_result_rejected():
    client = _client(
        lambda request: httpx.Response(200, json={"jsonrpc": "2.0", "result": [1, 2]})
    )
    with pytest.raises(MCPProtocolError):
        await client._request("tools/call")


async def test_request_ids_increment():
    """每请求一个自增 id：JSON-RPC 靠它配对响应。"""
    seen: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content)["id"])
        return httpx.Response(200, json={"jsonrpc": "2.0", "result": {}})

    client = _client(handler)
    await client._request("a")
    await client._request("b")

    assert seen == [1, 2]


def test_error_hierarchy_is_catchable_as_mcp_error():
    for exc_type in (MCPAuthError, MCPTransportError, MCPProtocolError):
        assert issubclass(exc_type, MCPError)
