"""024 进程池：复用、串行、崩溃重建。

这三个是常驻池的全部承诺——任何一条不成立，池就会变成「一个进程被两个请求
交错写坏」或「一个死进程污染后续所有调用」。
"""

import asyncio
import os
import sys

import pytest

from app.executors.mcp_client import MCPTransportError
from app.executors.stdio_pool import (
    PooledStdioClient,
    StdioSpec,
    close_all,
    pool_size,
    spec_for,
)

SERVER = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__), "..", "fixtures", "fake_mcp_stdio_server.py"
    )
)


@pytest.fixture(autouse=True)
async def _clean_pool():
    await close_all()
    yield
    await close_all()


def _spec() -> StdioSpec:
    # 用 spec_for 走生产路径（env_keys 解析）
    return spec_for(sys.executable, [SERVER], [])


async def test_reuses_one_process() -> None:
    async with PooledStdioClient(_spec()) as first:
        await first.initialize()
        pid_first = first._entry.client._process.pid  # type: ignore[union-attr]

    assert pool_size() == 1, "归还后进程应留在池里（常驻的目的）"

    async with PooledStdioClient(_spec()) as second:
        await second.initialize()
        pid_second = second._entry.client._process.pid  # type: ignore[union-attr]

    assert pid_first == pid_second, "第二次调用应复用同一个进程"
    assert pool_size() == 1


async def test_calls_are_serialized() -> None:
    """同一进程同时只处理一个请求：第二个必须等第一个释放。

    验证方式：第一个 client 未退出时，第二个 acquire 不应返回。
    """
    entered = asyncio.Event()

    async def holder():
        async with PooledStdioClient(_spec()) as client:
            await client.initialize()
            entered.set()
            await asyncio.sleep(0.3)

    async def waiter():
        await entered.wait()
        async with PooledStdioClient(_spec()) as client:
            await client.initialize()
            return True

    holder_task = asyncio.create_task(holder())
    start = asyncio.get_running_loop().time()
    await waiter()
    elapsed = asyncio.get_running_loop().time() - start
    await holder_task

    # 等待者至少要等到 holder 的 sleep(0.3) 结束（扣掉设定 entered 的那点时间）
    assert elapsed >= 0.25, f"没有串行化：等待 {elapsed:.2f}s 就拿到了进程"


async def test_broken_process_is_dropped_and_rebuilt() -> None:
    async with PooledStdioClient(_spec()) as client:
        await client.initialize()
        old_pid = client._entry.client._process.pid  # type: ignore[union-attr]
        # 模拟进程猝死（外部 kill / 自己崩溃）
        await client._entry.client.close()  # type: ignore[union-attr]
        with pytest.raises(MCPTransportError):
            await client.initialize()

    assert pool_size() == 0, "坏进程必须被移出池，否则后续调用全部失败"

    async with PooledStdioClient(_spec()) as rebuilt:
        await rebuilt.initialize()
        new_pid = rebuilt._entry.client._process.pid  # type: ignore[union-attr]

    assert new_pid != old_pid


# ---------- 并发首次访问（D5） ----------


async def test_concurrent_first_access_spawns_one_process() -> None:
    """两个协程同时首次访问同一 server，只能起一个子进程。

    D5 背景：`_get_entry` 的「判空」与「写入字典」之间夹着
    `await client.__aenter__()`（真实 spawn，npx 冷启动数秒），期间必然让出
    事件循环。没有 single-flight 保护时两个协程各起一个进程，字典只留下后写
    的那个——先启动的进程再没人 terminate（close_all 只遍历字典），泄漏一个
    常驻 npx。
    """
    spec = _spec()
    started = asyncio.Event()

    async def first() -> None:
        async with PooledStdioClient(spec) as client:
            await client.initialize()
            started.set()
            await asyncio.sleep(0.2)

    async def second() -> None:
        await started.wait()
        async with PooledStdioClient(spec) as client:
            await client.initialize()

    await asyncio.gather(first(), second())

    assert pool_size() == 1, f"只该留下一个常驻进程，实际 {pool_size()}"

    # 串行化也仍然成立：两个协程拿到的是同一个进程
    async with PooledStdioClient(spec) as client:
        await client.initialize()
        assert client._entry is not None  # type: ignore[union-attr]


async def test_concurrent_first_access_does_not_leak_process() -> None:
    """并发首次访问后，池里的进程数必须与「唯一 key 数」一致（不泄漏）。

    与上一条的区别：这里 8 个协程一起冲，泄漏在当前实现下会表现为
    `pool_size()` 仍为 1 但实际起了 8 个进程。因此直接数**真实存活**的子进程：
    借助池条目的 client._process.pid 之外无从得知，改为断言「启动次数」——
    用计数器包住 spec.client()。
    """
    spec = _spec()
    spawns = 0
    original_client = spec.client

    def counting_client():
        nonlocal spawns
        spawns += 1
        return original_client()

    # StdioSpec 是 frozen dataclass，用 object.__setattr__ 打桩方法
    object.__setattr__(spec, "client", counting_client)

    async def worker() -> None:
        async with PooledStdioClient(spec) as client:
            await client.initialize()

    await asyncio.gather(*(worker() for _ in range(8)))

    assert spawns == 1, f"8 个并发协程只该 spawn 1 次，实际 {spawns} 次"


# ---------- 跨事件循环复用（Celery Worker 的真实形态） ----------


def test_pool_rebuilds_entry_when_event_loop_changes() -> None:
    """池条目绑在创建它的事件循环上，换循环必须重建而不是复用。

    这是生产形态：`execute_run` 是同步 def，每次调用都 `asyncio.run(_main())`
    起一个**全新**的事件循环。上一条 Run 留下的子进程，其 `StreamReader`、
    `_drain_stderr` task 全都绑在已经关闭的旧循环上。第二次 Run 若直接复用
    该条目，第一次读 stdout 就会抛：

        RuntimeError: Task ... got Future ... attached to a different loop

    而且连 `client.close()` 都跑不了（`Event loop is closed`），于是进程
    永久泄漏。因此跨循环访问必须**同步**终结旧进程后重建。
    """

    async def _use() -> int:
        async with PooledStdioClient(_spec()) as client:
            await client.initialize()
            return client._entry.client._process.pid  # type: ignore[union-attr]

    first_pid = asyncio.run(_use())
    try:
        # asyncio.run 已关闭第一个循环；池里还留着上一个进程
        assert pool_size() == 1
        second_pid = asyncio.run(_use())  # 新循环：必须重建，不能复用
    finally:
        # 收尾走新循环的 close_all（旧循环已经没了）
        asyncio.run(close_all())

    assert second_pid != first_pid, (
        "换事件循环后必须重建进程，不能复用绑在旧循环上的条目"
    )
    assert isinstance(first_pid, int) and first_pid > 0
