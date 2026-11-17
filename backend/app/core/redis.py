"""进程级异步 Redis 客户端（连接池复用）。

问题：`token_store` / `login_limiter` / `events` 原先各自 `aioredis.from_url(...)`
用完 `aclose()`。于是：
- 一次登录 = 3 次建连（查限流 + 清计数 + 登记 refresh jti）；
- 每条 run 事件 = 1 次建连。

Redis 建连要 TCP 握手 + AUTH，这些成本全落在请求/执行的关键路径上。

做法：每个事件循环一个单例。
- 生产（uvicorn）整个进程只有一个 loop，等于全局一个连接池；
- 测试里可能 `asyncio.run(...)` 起临时 loop，按 loop 隔离才不会踩
  「attached to a different loop」；
- 循环关闭后旧客户端只丢弃不等待，避免在同步函数里做 async 清理。

注意：Redis 不可用不应成为单点故障——调用方仍保留 fail-open 语义，
这里只负责复用连接，不改变任何容错策略。
"""

from __future__ import annotations

import asyncio

import redis.asyncio as aioredis
import structlog

from app.core.config import settings

logger = structlog.get_logger(__name__)

_client: aioredis.Redis | None = None
_client_loop: asyncio.AbstractEventLoop | None = None


def get_redis() -> aioredis.Redis:
    """取当前事件循环的 Redis 单例（懒创建）。"""
    global _client, _client_loop

    loop = asyncio.get_running_loop()
    if _client is None or _client_loop is not loop or loop.is_closed():
        _client = aioredis.from_url(
            settings.redis_url,
            decode_responses=True,
            health_check_interval=30,
            socket_connect_timeout=2.0,
        )
        _client_loop = loop
    return _client


async def close_redis() -> None:
    """关闭并重置单例（应用关停 / 测试清理）。"""
    global _client, _client_loop

    client, _client, _client_loop = _client, None, None
    if client is None:
        return
    try:
        await client.aclose()
    except Exception as exc:  # noqa: BLE001 - 关停路径不应因清理失败而报错
        logger.warning("redis_close_failed", error=type(exc).__name__)
