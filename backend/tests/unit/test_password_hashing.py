"""密码哈希的异步行为（bcrypt 不得阻塞事件循环）。

背景：bcrypt 是纯 CPU 密集操作（cost=12，单次 verify ≈174ms）。此前
`auth_service.login/register` 在 async 函数里直接同步调用，API 只有 2 个
worker，几个并发登录就能把事件循环钉死（实测 4 次并发 verify 造成 677ms
完全停摆，期间所有接口不响应）。

这里钉住三件事：
1. `verify_password_async` 执行期间事件循环仍在转动（心跳不中断）；
2. 假哈希（用于「邮箱不存在」时消耗等量 CPU，防用户枚举）必须是**合法**
   bcrypt 串 —— 手写一个形似的字符串会让 checkpw 立刻抛 "Invalid salt"，
   而 verify_password 会把 ValueError 吞成 False，时序防护静默失效；
3. 存在路径与不存在路径的耗时量级一致。
"""

import asyncio
import time

import pytest

from app.core.security import (
    hash_password,
    hash_password_async,
    verify_password,
    verify_password_async,
)

# 与 auth_service 中一致的判定：合法 bcrypt 串以 $2b$<cost>$ 开头且总长 60
_BCRYPT_PREFIX = "$2b$12$"


async def _loop_stall_ms(coro_factory) -> tuple[float, float]:
    """执行 coro_factory() 期间，测事件循环最长停摆时长。

    返回 (任务墙钟 ms, 心跳最大间隔 ms)。
    """
    gaps: list[float] = []
    stop = False

    async def heartbeat() -> None:
        last = time.perf_counter()
        while not stop:
            await asyncio.sleep(0.005)
            now = time.perf_counter()
            gaps.append((now - last) * 1000)
            last = now

    beat = asyncio.create_task(heartbeat())
    await asyncio.sleep(0)  # 让心跳先跑起来，否则测不到第一段阻塞
    started = time.perf_counter()
    await coro_factory()
    wall = (time.perf_counter() - started) * 1000
    stop = True
    await beat
    return wall, max(gaps)


async def test_verify_async_does_not_block_event_loop():
    hashed = hash_password("secret")

    async def sync_style() -> None:
        for _ in range(3):
            verify_password("secret", hashed)

    async def async_style() -> None:
        await asyncio.gather(
            *[verify_password_async("secret", hashed) for _ in range(3)]
        )

    _, sync_stall = await _loop_stall_ms(sync_style)
    _, async_stall = await _loop_stall_ms(async_style)

    # 同步版本会把循环钉住整个 bcrypt 时长（三次 ≈500ms），远高于心跳间隔
    assert sync_stall > 200, f"同步调用应造成明显停摆，实测仅 {sync_stall:.0f}ms"
    # 异步版本不应出现秒级停摆；放宽到 100ms 以容忍 CI 上的调度抖动
    assert async_stall < 100, f"to_thread 版仍阻塞事件循环 {async_stall:.0f}ms"


async def test_hash_async_matches_sync_format():
    hashed = await hash_password_async("another-secret")
    assert hashed.startswith(_BCRYPT_PREFIX)
    assert len(hashed) == 60
    assert verify_password("another-secret", hashed)


async def test_dummy_password_hash_is_valid_bcrypt():
    """假哈希必须合法：非法会让时序防护静默失效（checkpw 立刻抛错被吞成 False）。

    只断言前缀是不够的 —— 一个形似 "$2b$12$xxxx" 但校验和不对的字符串前缀也是对的，
    checkpw 会立刻抛 "Invalid salt"。所以这里必须断言「真的跑完了一次 bcrypt」，
    用耗时下限作为代理：cost=12 一次约 170ms，非法串是 0ms。
    """
    from app.services.auth_service import _dummy_password_hash

    dummy = await _dummy_password_hash()
    assert dummy.startswith(_BCRYPT_PREFIX), f"非法假哈希: {dummy[:20]}"
    assert len(dummy) == 60

    started = time.perf_counter()
    assert verify_password("anything", dummy) is False
    elapsed_ms = (time.perf_counter() - started) * 1000

    # 真正执行了 bcrypt 才会达到这个量级；秒回说明哈希非法（被 ValueError 吞掉）
    assert elapsed_ms > 50, (
        f"假哈希未真正执行 bcrypt（仅 {elapsed_ms:.1f}ms）—— 极可能是非法 salt，"
        "时序防护已失效"
    )


async def test_unknown_email_costs_similar_time_to_wrong_password():
    """不存在的邮箱不应秒回 —— 否则响应时间可用来枚举已注册邮箱。"""
    from app.services.auth_service import _dummy_password_hash

    real = hash_password("RealPass123!")
    dummy = await _dummy_password_hash()

    started = time.perf_counter()
    verify_password("WrongPass", real)
    existing_ms = (time.perf_counter() - started) * 1000

    started = time.perf_counter()
    verify_password("WrongPass", dummy)
    missing_ms = (time.perf_counter() - started) * 1000

    # 同一 cost 的两条路径，允许 2x 浮动（机器负载抖动）
    ratio = max(existing_ms, missing_ms) / max(min(existing_ms, missing_ms), 1e-6)
    assert ratio < 2.0, (
        f"存在/不存在路径耗时差异过大：{existing_ms:.0f}ms vs {missing_ms:.0f}ms（比值 {ratio:.2f}）"
    )


@pytest.mark.parametrize("bad", ["", "not-a-hash", "$2b$12$short"])
def test_verify_password_tolerates_malformed_hash(bad):
    """畸形哈希不得抛异常（ValueError 要吞成 False）——老数据/误写都可能命中。"""
    assert verify_password("x", bad) is False
