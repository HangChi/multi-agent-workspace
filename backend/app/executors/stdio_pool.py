"""stdio MCP 的进程池（024）：常驻进程 + 串行请求 + 崩溃重建。

## 为什么是常驻池

`npx -y xxx` 冷启动要几秒。如果每次工具调用都启一次，一条 5 步的 ReAct 轨迹里
光启动就吃掉十几秒，而且每次都要重新初始化会话（很多 MCP server 在内存里存状态：
浏览器 session、登录 token、打开的文档——重启即丢）。

## 为什么是「单进程串行」

一个 stdio 进程只有一对 stdin/stdout。两个请求同时写进去，响应会交错，
`_request` 的「读一行、校验 id」就会拿到别人的结果。所以每个进程配一把
`asyncio.Lock`：同一时刻只有一个调用在用，其余排队。

代价是并发调用同一 server 会串行化——这符合 stdio MCP server 的实际能力
（它们本来就是给「一个客户端、顺序对话」设计的）。

## 崩溃重建

任何 `MCPTransportError`（进程退出、超时读到 EOF）都会把该进程标记为 broken：
释放锁时关闭并移出池，下次调用重新启动。**不让坏进程留在池里**——否则一次崩溃
会污染后续所有调用，而错误信息还指向一个已经死掉的进程。

## 陈旧回收

条目带 `last_used`，超过 `IDLE_TTL_SECONDS` 的在下一次 acquire 时关闭重建。
不做后台定时器：worker 里的后台任务生命周期很难管，而「下次用的时候顺手清」
对内存与 fd 泄漏同样有效。
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass, field
from typing import Any, Self

from app.executors.mcp_client import MCPTransportError
from app.executors.stdio_mcp_client import (
    DEFAULT_ALLOWED_COMMANDS,
    StdioMCPClient,
)

IDLE_TTL_SECONDS = 300.0


@dataclass(frozen=True)
class StdioSpec:
    """启动一个 stdio server 所需的全部信息（可哈希，用作池的键）。"""

    command: str
    args: tuple[str, ...] = ()
    # 只放「名字 → 值」里由服务端补全的部分（见 resolve_env_keys）
    env: tuple[tuple[str, str], ...] = ()
    allowed_commands: tuple[str, ...] = DEFAULT_ALLOWED_COMMANDS

    @property
    def key(self) -> str:
        env_part = ",".join(f"{k}={v}" for k, v in self.env)
        return f"{self.command}\x00{' '.join(self.args)}\x00{env_part}"

    def client(self) -> StdioMCPClient:
        return StdioMCPClient(
            self.command,
            list(self.args),
            env=dict(self.env),
            allowed_commands=self.allowed_commands,
        )


def resolve_env_keys(keys: list[str] | None) -> tuple[tuple[str, str], ...]:
    """把「变量名清单」解析成当前环境里的值。

    值在**运行时**从 worker 进程环境取，不落库：库里存了值等于多一份密钥副本，
    而且要处理轮换。取不到的名字直接跳过（server 自己会因为缺配置报错，比我们
    凭空造一个空值更容易诊断）。
    """
    resolved: list[tuple[str, str]] = []
    for key in keys or []:
        value = os.environ.get(key)
        if value is not None:
            resolved.append((key, value))
    return tuple(resolved)


def spec_for(
    command: str, args: list[str] | None, env_keys: list[str] | None
) -> StdioSpec:
    """从 server 行构造 spec（env 值按名字从当前环境取）。"""
    return StdioSpec(
        command=command,
        args=tuple(args or []),
        env=resolve_env_keys(env_keys),
    )


@dataclass
class _Entry:
    client: StdioMCPClient
    lock: asyncio.Lock
    last_used: float = field(default_factory=time.monotonic)

    def expired(self) -> bool:
        return (time.monotonic() - self.last_used) > IDLE_TTL_SECONDS


_entries: dict[str, _Entry] = {}


async def _get_entry(spec: StdioSpec) -> _Entry:
    entry = _entries.get(spec.key)
    if entry is not None:
        return entry
    client = spec.client()
    await client.__aenter__()
    entry = _Entry(client=client, lock=asyncio.Lock())
    _entries[spec.key] = entry
    return entry


async def _drop_entry(spec: StdioSpec) -> None:
    entry = _entries.pop(spec.key, None)
    if entry is not None:
        await entry.client.close()


class PooledStdioClient:
    """借一个常驻进程，用完归还（不关进程）。

    形状与 `MCPClient` 一致（async context manager + initialize/list_tools/
    call_tool），因此 `ExternalToolExecutor` 的 `client_factory` 可以直接换成
    它，不需要改 executor。
    """

    def __init__(self, spec: StdioSpec) -> None:
        self.spec = spec
        self._entry: _Entry | None = None
        self._broken = False

    async def __aenter__(self) -> Self:
        entry = await _get_entry(self.spec)
        await entry.lock.acquire()
        # 拿到锁之后再判断是否陈旧：避免关掉一个正在被别的协程使用的进程
        if entry.expired():
            await _drop_entry(self.spec)
            entry = await _get_entry(self.spec)
            await entry.lock.acquire()
        entry.last_used = time.monotonic()
        self._entry = entry
        return self

    async def __aexit__(self, *_exc: object) -> None:
        entry = self._entry
        self._entry = None
        if entry is None:
            return
        if self._broken:
            await _drop_entry(self.spec)
        entry.lock.release()

    # ---------- 转发（任何传输错误都标记 broken） ----------

    async def _call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        entry = self._entry
        if entry is None:
            raise MCPTransportError("连接尚未建立")
        try:
            return await getattr(entry.client, method)(*args, **kwargs)
        except MCPTransportError:
            self._broken = True
            raise

    async def initialize(self) -> dict[str, Any]:
        return await self._call("initialize")

    async def list_tools(self) -> list[Any]:
        return await self._call("list_tools")

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        return await self._call("call_tool", name, arguments or {}, timeout=timeout)

    async def cancel(self, request_id: str | int) -> None:
        await self._call("cancel", request_id)


def build_stdio_client_factory(spec: StdioSpec):
    """生成 `MCPClient(endpoint, headers=..., call_timeout=...)` 形状的工厂。

    executor 会传 `headers` 与 `call_timeout`：stdio 没有 HTTP 头，`headers`
    直接忽略；超时由调用方在 `call_tool(timeout=...)` 上再次收敛。
    """

    def factory(_endpoint: str, **_kwargs: Any) -> PooledStdioClient:
        return PooledStdioClient(spec)

    return factory


async def close_all() -> None:
    """关闭所有常驻进程（worker 退出时调用，避免留下孤儿子进程）。"""
    keys = list(_entries)
    for key in keys:
        entry = _entries.pop(key, None)
        if entry is not None:
            await entry.client.close()


def pool_size() -> int:
    return len(_entries)
