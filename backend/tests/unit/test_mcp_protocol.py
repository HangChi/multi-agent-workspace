import httpx
import pytest

from app.executors.mcp_client import MCPClient, MCPProtocolError


class StubTransport(httpx.AsyncBaseTransport):
    def __init__(self, responder):
        self.responder = responder
        self.requests = []

    async def handle_async_request(self, request):
        self.requests.append(request)
        payload = self.responder(request)
        return httpx.Response(200, json=payload, request=request)


@pytest.mark.asyncio
async def test_initialize_and_discover_complete_paginated_tool_list():
    def respond(request):
        message = __import__("json").loads(request.content)
        if "id" not in message:
            return {}
        if message["method"] == "initialize":
            return {"jsonrpc": "2.0", "id": message["id"], "result": {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}, "serverInfo": {"name": "test", "version": "1"}}}
        if message["method"] == "tools/list" and not message.get("params", {}).get("cursor"):
            return {"jsonrpc": "2.0", "id": message["id"], "result": {"tools": [{"name": "first", "description": "first tool", "inputSchema": {"type": "object"}}], "nextCursor": "next"}}
        return {"jsonrpc": "2.0", "id": message["id"], "result": {"tools": [{"name": "second", "description": "second tool", "inputSchema": {"type": "object"}}]}}

    transport = StubTransport(respond)
    async with httpx.AsyncClient(transport=transport) as http:
        client = MCPClient("https://mcp.example.test/mcp", http_client=http)
        await client.initialize()
        tools = await client.list_tools()
    assert [tool.name for tool in tools] == ["first", "second"]
    assert len([r for r in transport.requests if __import__("json").loads(r.content)["method"] == "tools/list"]) == 2


@pytest.mark.asyncio
async def test_rejects_duplicate_or_malformed_tool_definitions():
    def respond(request):
        message = __import__("json").loads(request.content)
        if "id" not in message:
            return {}
        if message["method"] == "initialize":
            return {"jsonrpc": "2.0", "id": message["id"], "result": {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}, "serverInfo": {"name": "test", "version": "1"}}}
        return {"jsonrpc": "2.0", "id": message["id"], "result": {"tools": [{"name": "same", "description": "one", "inputSchema": {"type": "object"}}, {"name": "same", "description": "two", "inputSchema": {"type": "object"}}]}}

    async with httpx.AsyncClient(transport=StubTransport(respond)) as http:
        client = MCPClient("https://mcp.example.test/mcp", http_client=http)
        await client.initialize()
        with pytest.raises(MCPProtocolError, match="duplicate"):
            await client.list_tools()


@pytest.mark.asyncio
async def test_rejects_json_rpc_error_and_unsupported_protocol_version():
    def respond(request):
        message = __import__("json").loads(request.content)
        return {"jsonrpc": "2.0", "id": message["id"], "result": {"protocolVersion": "unsupported", "capabilities": {}, "serverInfo": {"name": "test", "version": "1"}}}

    async with httpx.AsyncClient(transport=StubTransport(respond)) as http:
        client = MCPClient("https://mcp.example.test/mcp", http_client=http)
        with pytest.raises(MCPProtocolError):
            await client.initialize()
