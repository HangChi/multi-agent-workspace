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
