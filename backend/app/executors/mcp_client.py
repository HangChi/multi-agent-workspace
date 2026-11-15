"""Bounded MCP Streamable HTTP client used only by the Worker."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Self

import httpx

from app.core.config import settings


class MCPError(RuntimeError):
    """Base class for safe external MCP failures."""


class MCPTransportError(MCPError):
    """Connection, HTTP or response decoding failure."""


class MCPProtocolError(MCPError):
    """Malformed or unsupported JSON-RPC/MCP response."""


class MCPToolError(MCPError):
    """A valid MCP tools/call result with isError=true."""


@dataclass(frozen=True)
class MCPTool:
    name: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] | None = None


class MCPClient:
    """Minimal protocol client with injected HTTP transport for deterministic tests."""

    def __init__(
        self,
        endpoint: str,
        *,
        http_client: httpx.AsyncClient | None = None,
        max_response_bytes: int | None = None,
        handshake_timeout: float | None = None,
        call_timeout: float | None = None,
    ) -> None:
        self.endpoint = endpoint
        self._http = http_client
        self._owned_http: httpx.AsyncClient | None = None
        self._next_id = 0
        self._initialized = False
        self.max_response_bytes = max_response_bytes or settings.mcp_max_response_bytes
        self.handshake_timeout = handshake_timeout or settings.mcp_handshake_timeout_seconds
        self.call_timeout = call_timeout or settings.mcp_call_timeout_seconds

    async def __aenter__(self) -> Self:
        if self._http is None:
            self._owned_http = httpx.AsyncClient(
                follow_redirects=False,
                timeout=httpx.Timeout(self.call_timeout),
                limits=httpx.Limits(max_connections=settings.mcp_max_concurrency_per_server),
            )
            self._http = self._owned_http
        return self

    async def __aexit__(self, *_exc: object) -> None:
        if self._owned_http is not None:
            await self._owned_http.aclose()
            self._owned_http = None
            self._http = None

    def _id(self) -> int:
        self._next_id += 1
        return self._next_id

    async def _request(self, method: str, params: dict[str, Any] | None = None, *, timeout: float | None = None) -> dict[str, Any]:
        if self._http is None:
            raise MCPTransportError("MCP client must be used as an async context manager or receive an HTTP client")
        message: dict[str, Any] = {"jsonrpc": "2.0", "id": self._id(), "method": method}
        if params is not None:
            message["params"] = params
        try:
            response = await self._http.post(
                self.endpoint,
                json=message,
                headers={"Accept": "application/json, text/event-stream", "Content-Type": "application/json"},
                follow_redirects=False,
                timeout=timeout or self.call_timeout,
            )
            response.raise_for_status()
        except (httpx.HTTPError, TimeoutError) as exc:
            raise MCPTransportError("MCP server request failed") from exc
        if len(response.content) > self.max_response_bytes:
            raise MCPTransportError("MCP server response exceeded the size limit")
        try:
            payload = response.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise MCPProtocolError("MCP server returned invalid JSON") from exc
        if not isinstance(payload, dict) or payload.get("jsonrpc") != "2.0":
            raise MCPProtocolError("MCP server returned an invalid JSON-RPC envelope")
        if payload.get("error") is not None:
            error = payload.get("error")
            message_text = error.get("message", "protocol error") if isinstance(error, dict) else "protocol error"
            raise MCPProtocolError(str(message_text)[:200])
        result = payload.get("result")
        if not isinstance(result, dict):
            raise MCPProtocolError("MCP response is missing an object result")
        return result

    async def initialize(self) -> dict[str, Any]:
        result = await self._request(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "agent-workbench", "version": "0.1.0"},
            },
            timeout=self.handshake_timeout,
        )
        version = result.get("protocolVersion")
        if version != "2025-06-18":
            raise MCPProtocolError("MCP protocol version is not supported")
        self._initialized = True
        # Notifications have no response; servers that do not need it still accept the call.
        if self._http is not None:
            try:
                await self._http.post(
                    self.endpoint,
                    json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                    headers={"Accept": "application/json, text/event-stream", "Content-Type": "application/json"},
                    follow_redirects=False,
                    timeout=self.handshake_timeout,
                )
            except httpx.HTTPError as exc:
                raise MCPTransportError("MCP initialized notification failed") from exc
        return result

    async def list_tools(self) -> list[MCPTool]:
        if not self._initialized:
            raise MCPProtocolError("MCP client is not initialized")
        cursor: str | None = None
        tools: list[MCPTool] = []
        seen: set[str] = set()
        while True:
            params = {"cursor": cursor} if cursor else None
            result = await self._request("tools/list", params, timeout=self.handshake_timeout)
            page = result.get("tools")
            if not isinstance(page, list):
                raise MCPProtocolError("MCP tools/list result is missing tools")
            for raw in page:
                if not isinstance(raw, dict) or not isinstance(raw.get("name"), str) or not raw.get("name"):
                    raise MCPProtocolError("MCP tool definition is missing a name")
                name = raw["name"]
                if name in seen:
                    raise MCPProtocolError(f"duplicate MCP tool name: {name}")
                if not isinstance(raw.get("description"), str) or not isinstance(raw.get("inputSchema"), dict):
                    raise MCPProtocolError(f"MCP tool definition is incomplete: {name}")
                seen.add(name)
                tools.append(MCPTool(name=name, description=raw["description"][:4000], input_schema=raw["inputSchema"], output_schema=raw.get("outputSchema")))
                if len(tools) > settings.mcp_max_tools_per_server:
                    raise MCPProtocolError("MCP server returned too many tools")
            next_cursor = result.get("nextCursor")
            if not next_cursor:
                return tools
            if not isinstance(next_cursor, str) or next_cursor == cursor:
                raise MCPProtocolError("MCP tools/list pagination cursor is invalid")
            cursor = next_cursor

    async def call_tool(self, name: str, arguments: dict[str, Any], *, timeout: float | None = None) -> dict[str, Any]:
        if not self._initialized:
            raise MCPProtocolError("MCP client is not initialized")
        result = await self._request("tools/call", {"name": name, "arguments": arguments}, timeout=timeout)
        if result.get("isError") is True:
            raise MCPToolError("MCP tool returned an error")
        return result

    async def cancel(self, request_id: str) -> None:
        if self._http is None:
            return
        try:
            await self._http.post(
                self.endpoint,
                json={"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": request_id, "reason": "run cancelled"}},
                headers={"Accept": "application/json, text/event-stream", "Content-Type": "application/json"},
                follow_redirects=False,
                timeout=1.0,
            )
        except httpx.HTTPError:
            # Cancellation is best-effort; the caller still discards late results.
            return
