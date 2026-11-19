"""MCP 工具列表与调用的协议校验单测（015 覆盖率补齐）。

`list_tools` 要面对的是「第三方 MCP 服务返回了什么」——它不能假设对方守规矩：

- 没初始化就调 → 明确报错（否则会发出缺 initialize 的请求，对端行为不可预期）；
- tools 缺失 / 工具没名字 / 名字重复 / description 或 inputSchema 类型不对
  → 一律协议错误。**重复名字尤其危险**：授权与审计都按名字索引，重名会让
  「调了 A 却记成 B」；
- 分页游标不前进（对端返回同一个 cursor）必须报错，否则无限循环。
"""

import pytest

from app.executors.mcp_client import (
    MCPClient,
    MCPProtocolError,
    MCPTool,
    MCPToolError,
)


def _client(result: dict) -> MCPClient:
    """构造一个「已初始化」的客户端，_request 直接返回给定结果。"""
    client = MCPClient("https://mcp.test/rpc")
    client._initialized = True

    async def _fake_request(method, params=None, *, timeout=None):
        return result

    client._request = _fake_request  # type: ignore[method-assign]
    return client


async def test_list_tools_requires_initialization():
    client = MCPClient("https://mcp.test/rpc")
    with pytest.raises(MCPProtocolError):
        await client.list_tools()


async def test_list_tools_parses_definitions():
    client = _client(
        {
            "tools": [
                {
                    "name": "search",
                    "description": "搜索",
                    "inputSchema": {"type": "object"},
                    "outputSchema": {"type": "object"},
                }
            ]
        }
    )

    tools = await client.list_tools()

    assert tools == [
        MCPTool(
            name="search",
            description="搜索",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
        )
    ]


async def test_list_tools_rejects_missing_tools_key():
    with pytest.raises(MCPProtocolError):
        await _client({"nope": []}).list_tools()


async def test_list_tools_rejects_unnamed_tool():
    with pytest.raises(MCPProtocolError):
        await _client({"tools": [{"description": "x", "inputSchema": {}}]}).list_tools()


async def test_list_tools_rejects_duplicate_names():
    """重名会让「调了 A 却记成 B」——授权与审计都按名字索引。"""
    duplicate = {"name": "search", "description": "x", "inputSchema": {}}
    with pytest.raises(MCPProtocolError):
        await _client({"tools": [duplicate, dict(duplicate)]}).list_tools()


async def test_list_tools_rejects_incomplete_definition():
    with pytest.raises(MCPProtocolError):
        await _client({"tools": [{"name": "search", "description": 123}]}).list_tools()


async def test_list_tools_rejects_stuck_cursor():
    """游标不前进就是死循环：必须显式报错。"""
    with pytest.raises(MCPProtocolError):
        await _client({"tools": [], "nextCursor": "same"}).list_tools()


async def test_call_tool_requires_initialization():
    client = MCPClient("https://mcp.test/rpc")
    with pytest.raises(MCPProtocolError):
        await client.call_tool("search", {})


async def test_call_tool_raises_on_is_error():
    """MCP 的「工具执行失败」在响应体里是 isError，不是 HTTP 错误码。"""
    client = _client({"isError": True, "content": []})
    with pytest.raises(MCPToolError):
        await client.call_tool("search", {})


async def test_call_tool_returns_result():
    client = _client({"isError": False, "content": [{"type": "text", "text": "ok"}]})
    result = await client.call_tool("search", {"q": "x"})
    assert result["content"][0]["text"] == "ok"
