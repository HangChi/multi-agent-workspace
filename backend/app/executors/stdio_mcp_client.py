"""MCP stdio 传输客户端（024）：把本地子进程当成 MCP server 用。

为什么需要它：003 只支持 Streamable HTTP——也就是「对方的 MCP server 已经跑在
某个 URL 上」。而社区里大量 MCP server 只提供 stdio 入口（`npx -y @xxx/mcp`、
`uvx xxx-mcp`），它们**不是一个服务**，而是一个进程：启动、说 JSON-RPC、退出。

## 三条安全底线

1. **不经过 shell**：`create_subprocess_exec` 直接 exec 命令，参数以数组传递。
   `shell=True` + 用户可控参数 = 命令注入（`; rm -rf /`）。
2. **命令白名单**：只允许配置里的可执行文件（默认 npx / uvx / python / node）。
   否则这个功能等价于「让任何 member 在 worker 容器里执行任意命令」。
3. **环境变量白名单**：只透传 PATH / HOME / LANG 这少数几个，其余一律不传。
   否则 `AWS_SECRET_ACCESS_KEY`、`DATABASE_URL` 会原样送进第三方进程。

## 协议

MCP 的 stdio 传输是 **newline-delimited JSON-RPC**：一行一条消息。
这里的 `_request` 假设「请求-响应严格交替」（MCP 的 stdio 允许 server 主动
发 notification，所以读取时跳过没有 `id` 的行，而不是当成错误）。

## 与 HTTP 客户端的边界

本模块只做「协议 + 进程生命周期」。它与 `MCPClient`（HTTP）**接口一致**
（initialize / list_tools / call_tool），但不接管 003 的注册、发现与调用链——
那部分依赖 `endpoint_url` 的 URL 语义（SSRF 校验、归一化、快照），
改造需要 transport 分叉，见 spec 的「未接线部分」。
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Self

from app.executors.mcp_client import (
    MCPProtocolError,
    MCPTool,
    MCPToolError,
    MCPTransportError,
)

DEFAULT_ALLOWED_COMMANDS = ("npx", "uvx", "python", "python3", "node")
# 只透传这几个环境变量（含 PATH，否则 npx 找不到 node）。
ENV_ALLOWLIST = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "TZ", "SYSTEMROOT")

_DEFAULT_HANDSHAKE_TIMEOUT = 20.0
_DEFAULT_CALL_TIMEOUT = 60.0
_DEFAULT_MAX_BYTES = 2 * 1024 * 1024
_STDERR_TAIL = 4096


class StdioCommandRejected(RuntimeError):
    """命令不在白名单里（配置问题，不是运行时错误）。"""


class StdioMCPClient:
    """一个 stdio MCP server 的连接（async context manager）。

    用法：
        async with StdioMCPClient("npx", ["-y", "@scope/server"]) as client:
            await client.initialize()
            tools = await client.list_tools()
            result = await client.call_tool("search", {"q": "x"})
    """

    def __init__(
        self,
        command: str,
        args: list[str] | None = None,
        *,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
        allowed_commands: tuple[str, ...] = DEFAULT_ALLOWED_COMMANDS,
        max_response_bytes: int = _DEFAULT_MAX_BYTES,
        handshake_timeout: float = _DEFAULT_HANDSHAKE_TIMEOUT,
        call_timeout: float = _DEFAULT_CALL_TIMEOUT,
    ) -> None:
        if os.path.basename(command) not in allowed_commands:
            raise StdioCommandRejected(
                f"命令不在白名单内：{command}（允许：{', '.join(allowed_commands)}）"
            )
        self.command = command
        self.args = list(args or [])
        self.env_overrides = dict(env or {})
        self.cwd = cwd
        self.max_response_bytes = max_response_bytes
        self.handshake_timeout = handshake_timeout
        self.call_timeout = call_timeout

        self._process: asyncio.subprocess.Process | None = None
        self._next_id = 0
        self._stderr_task: asyncio.Task[None] | None = None
        self._stderr_bytes = bytearray()

    # ---------- 生命周期 ----------

    async def __aenter__(self) -> Self:
        env = {k: os.environ[k] for k in ENV_ALLOWLIST if k in os.environ}
        env.update(self.env_overrides)
        try:
            # 刻意不用 shell：参数数组直接 exec
            self._process = await asyncio.create_subprocess_exec(
                self.command,
                *self.args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                cwd=self.cwd,
            )
        except FileNotFoundError as exc:
            raise MCPTransportError(f"命令不存在：{self.command}") from exc
        self._stderr_task = asyncio.create_task(self._drain_stderr())
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.close()

    async def close(self) -> None:
        """关 stdin → terminate → 超时 kill。确保不留僵尸进程。"""
        process = self._process
        if process is None:
            return
        self._process = None
        try:
            if process.stdin and not process.stdin.is_closing():
                process.stdin.close()
                await asyncio.wait_for(process.stdin.wait_closed(), timeout=2)
        except (TimeoutError, ConnectionResetError):
            pass
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=3)
            except TimeoutError:
                process.kill()
                await process.wait()
        if self._stderr_task is not None:
            self._stderr_task.cancel()
            self._stderr_task = None

    async def _drain_stderr(self) -> None:
        """持续读 stderr，只保留尾部（诊断用）。不读会写满管道把子进程卡死。"""
        assert self._process is not None and self._process.stderr is not None
        while True:
            chunk = await self._process.stderr.read(1024)
            if not chunk:
                return
            self._stderr_bytes.extend(chunk)
            if len(self._stderr_bytes) > _STDERR_TAIL:
                del self._stderr_bytes[: len(self._stderr_bytes) - _STDERR_TAIL]

    @property
    def stderr_tail(self) -> str:
        return self._stderr_bytes.decode(errors="replace")

    # ---------- JSON-RPC ----------

    def _id(self) -> int:
        self._next_id += 1
        return self._next_id

    async def _write(self, message: dict[str, Any]) -> None:
        process = self._process
        if process is None or process.stdin is None:
            raise MCPTransportError("进程未启动")
        payload = json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n"
        process.stdin.write(payload.encode())
        await process.stdin.drain()

    async def _read_message(self, timeout: float) -> dict[str, Any]:
        process = self._process
        if process is None or process.stdout is None:
            raise MCPTransportError("进程未启动")
        # 一行一条消息；跳过 server 主动发的 notification（没有 id）
        while True:
            line = await asyncio.wait_for(process.stdout.readline(), timeout=timeout)
            if not line:
                raise MCPTransportError(
                    f"server 已退出（stderr 尾部：{self.stderr_tail[-300:] or '空'}）"
                )
            if len(line) > self.max_response_bytes:
                raise MCPTransportError("响应超过大小上限")
            text = line.decode(errors="replace").strip()
            if not text:
                continue
            try:
                message = json.loads(text)
            except json.JSONDecodeError as exc:
                raise MCPProtocolError(f"非 JSON 响应：{text[:120]}") from exc
            if isinstance(message, dict) and "id" in message:
                return message
            # notification / 其它异步消息：忽略

    async def _request(
        self, method: str, params: dict[str, Any] | None = None, *, timeout: float
    ) -> dict[str, Any]:
        request_id = self._id()
        await self._write(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params or {},
            }
        )
        response = await self._read_message(timeout)
        if response.get("id") != request_id:
            raise MCPProtocolError("响应 id 与请求不匹配")
        error = response.get("error")
        if error:
            raise MCPProtocolError(
                f"{method} 失败：{error.get('message') if isinstance(error, dict) else error}"
            )
        result = response.get("result")
        return result if isinstance(result, dict) else {}

    # ---------- MCP 方法（与 HTTP 客户端同签名） ----------

    async def initialize(self) -> dict[str, Any]:
        result = await self._request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "agent-workbench", "version": "0.1"},
            },
            timeout=self.handshake_timeout,
        )
        # 通知（无 id、无需响应）
        await self._write({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return result

    async def list_tools(self) -> list[MCPTool]:
        result = await self._request("tools/list", timeout=self.handshake_timeout)
        tools: list[MCPTool] = []
        for raw in result.get("tools", []) or []:
            if not isinstance(raw, dict) or not raw.get("name"):
                continue
            tools.append(
                MCPTool(
                    name=str(raw["name"]),
                    description=str(raw.get("description") or ""),
                    input_schema=raw.get("inputSchema") or {},
                    output_schema=raw.get("outputSchema"),
                )
            )
        return tools

    async def call_tool(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        result = await self._request(
            "tools/call",
            {"name": name, "arguments": arguments or {}},
            timeout=self.call_timeout,
        )
        if result.get("isError"):
            raise MCPToolError(f"工具 {name} 返回错误")
        return result

    async def cancel(self, request_id: str | int) -> None:
        await self._write(
            {
                "jsonrpc": "2.0",
                "method": "notifications/cancelled",
                "params": {"requestId": request_id},
            }
        )
