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
    # 创建这个子进程的事件循环。Celery Worker 里 `execute_run` 是同步 def，
    # 每次调用都 `asyncio.run()` 起**全新**循环；上一条 Run 留下的条目，
    # 其 StreamReader / _drain_stderr task 全绑在已关闭的旧循环上，跨循环
    # 复用会抛 "got Future attached to a different loop"，且连 close() 都
    # 跑不了（Event loop is closed）→ 进程永久泄漏。见 _get_entry 的重建逻辑。
    loop: asyncio.AbstractEventLoop
    last_used: float = field(default_factory=time.monotonic)

    def expired(self) -> bool:
        return (time.monotonic() - self.last_used) > IDLE_TTL_SECONDS

    def same_loop_as_current(self) -> bool:
        try:
            return asyncio.get_running_loop() is self.loop
        except RuntimeError:  # 无运行中的循环
            return False


_entries: dict[str, _Entry] = {}

# 同一个 key 同时只允许一个协程 spawn，其余就地等它——否则两个协程同时首次
# 访问同一 server 会各起一个子进程，字典只留下后写的那个，先启动的进程再没人
# terminate（close_all 只遍历字典）→ 泄漏一个常驻 npx。
#
# 为什么不是 `asyncio.Lock`：`_LoopBoundMixin` 会把锁绑定到**第一次争用**时的
# 事件循环，之后换循环（Celery Worker 每个任务都新起循环）再争用就抛
# "bound to a different event loop"。登记的 Future 天然属于发起者自己的循环，
# 没有这个包袱。
_inflight: dict[str, tuple[asyncio.AbstractEventLoop, asyncio.Future]] = {}


def _terminate_sync(client: StdioMCPClient) -> None:
    """不经过事件循环终结子进程（供跨循环场景使用）。

    `client.close()` 前半段是 async 的（`stdin.wait_closed()`），在已关闭的
    旧循环上连调用都会抛 `Event loop is closed`。但进程对象本身还在，直接
    用同步 API 发信号即可——这一步不需要循环。
    """
    process = getattr(client, "_process", None)
    if process is None:
        return
    client._process = None  # type: ignore[attr-defined]
    # 关掉 drain 任务：它绑在已死的循环上，只能取消，不能 await
    task = getattr(client, "_stderr_task", None)
    if task is not None:
        task.cancel()
        client._stderr_task = None  # type: ignore[attr-defined]
    if process.returncode is None:
        try:
            process.terminate()
        except (ProcessLookupError, OSError):
            return
        # 给一点时间自行退出；worker 是同步上下文，短暂阻塞可接受
        deadline = time.monotonic() + 3.0
        while process.returncode is None and time.monotonic() < deadline:
            time.sleep(0.05)
        if process.returncode is None:
            try:
                process.kill()
            except (ProcessLookupError, OSError):
                pass


def _drop_stale_entry(key: str) -> None:
    """丢弃一个不可安全复用的条目（进程同步终结，不碰事件循环）。"""
    entry = _entries.pop(key, None)
    if entry is not None:
        _terminate_sync(entry.client)


async def _spawn_locked(spec: StdioSpec) -> _Entry:
    """single-flight：同 key 并发首次访问只 spawn 一次，其余等这一次的结果。

    `_entries` 的判空与写入之间夹着 `await client.__aenter__()`（真实 spawn，
    npx 冷启动数秒），期间必然让出事件循环——没有这层保护就是 D5 的竞态。
    """
    loop = asyncio.get_running_loop()

    entry = _entries.get(spec.key)
    if entry is not None:
        if entry.same_loop_as_current():
            return entry
        # 同 key 但绑在别的事件循环上：先同步终结再重建
        _drop_stale_entry(spec.key)

    pending = _inflight.get(spec.key)
    if pending is not None:
        pending_loop, fut = pending
        if pending_loop is loop and not fut.done():
            # 已有同循环的协程正在 spawn：等它，不重复起进程
            await asyncio.shield(fut)
            ready = _entries.get(spec.key)
            if ready is not None and ready.same_loop_as_current():
                return ready
            # 对方建完又被清掉了（极罕见的并发 drop）：自己重来一次
            return await _spawn_locked(spec)
        # 属于已废弃的旧循环（永无协程来 await 它）：丢掉登记，自己起新的
        _inflight.pop(spec.key, None)

    fut = loop.create_future()
    _inflight[spec.key] = (loop, fut)
    try:
        client = spec.client()
        await client.__aenter__()
        created = _Entry(client=client, lock=asyncio.Lock(), loop=loop)
        _entries[spec.key] = created
        if not fut.done():
            fut.set_result(None)
        return created
    except BaseException as exc:
        # 让等待者一起失败，而不是各自再 spawn 一遍
        if not fut.done():
            fut.set_exception(exc)
            fut.exception()  # 取走异常，避免 "never retrieved" 噪音
        raise
    finally:
        token = _inflight.get(spec.key)
        if token is not None and token[1] is fut:
            _inflight.pop(spec.key, None)


async def _get_entry(spec: StdioSpec) -> _Entry:
    return await _spawn_locked(spec)


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
    """关闭所有常驻进程（worker 退出时调用，避免留下孤儿子进程）。

    条目可能绑在**别的**事件循环上（Worker 里每条 Run 都起新循环），那种
    情况下 `await client.close()` 会抛 `Event loop is closed`——所以按归属
    分流：同循环的正常异步关闭，跨循环的走同步终结。
    """
    loop = asyncio.get_running_loop()
    for key in list(_entries):
        entry = _entries.pop(key, None)
        if entry is None:
            continue
        if entry.loop is loop:
            try:
                await entry.client.close()
            except (RuntimeError, OSError):
                # 关闭途中的抖动不该拦住其余条目
                _terminate_sync(entry.client)
        else:
            _terminate_sync(entry.client)
    # 清掉可能残留的在途登记（它们属于已废弃的循环）
    _inflight.clear()


def close_all_sync() -> None:
    """同步版本：供 Celery 信号处理器（无事件循环）调用。"""
    for key in list(_entries):
        entry = _entries.pop(key, None)
        if entry is not None:
            _terminate_sync(entry.client)
    _inflight.clear()


def pool_size() -> int:
    return len(_entries)
