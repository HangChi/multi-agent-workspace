"""事件总线：Redis Pub/Sub 封装。

桥接 Worker → API → WebSocket 的实时事件链路（对齐 contracts/api.md 的事件流）：
- 发布端（Worker/编排）：publish_run_event(run_id, payload) / publish_request_event
- 订阅端（API/WS）：subscribe_run_events(run_id, handler) / subscribe_request_events

频道命名：run:{run_id} 与 request:{request_id}（007 新增——分派类事件发生在
Run 存在之前，塞不进 run 频道），payload 为 JSON 字符串。
"""

import json
from collections.abc import Awaitable, Callable
from typing import Any

import redis.asyncio as aioredis

from app.core.redis import get_redis

_run_event_prefix = "run:"
_request_event_prefix = "request:"


def channel_for_run(run_id: str) -> str:
    return f"{_run_event_prefix}{run_id}"


def channel_for_request(request_id: str) -> str:
    return f"{_request_event_prefix}{request_id}"


def _redis_client() -> aioredis.Redis:
    """复用进程级连接池（见 app.core.redis）。"""
    return get_redis()


async def publish_run_event(run_id: str, payload: dict[str, Any]) -> None:
    """向 run:{run_id} 频道发布一条事件。"""
    await _redis_client().publish(
        channel_for_run(run_id), json.dumps(payload, ensure_ascii=False)
    )


async def publish_request_event(request_id: str, payload: dict[str, Any]) -> None:
    """向 request:{request_id} 频道发布一条事件（007 分派类状态变更）。"""
    await _redis_client().publish(
        channel_for_request(request_id), json.dumps(payload, ensure_ascii=False)
    )


async def redis_ping() -> bool:
    """Redis 连通性探测（就绪检查用）。"""
    return bool(await _redis_client().ping())


EventHandler = Callable[[dict[str, Any]], Awaitable[None]]


async def subscribe_run_events(
    run_id: str,
    handler: EventHandler,
    *,
    on_ready: Callable[[], Awaitable[None]] | None = None,
) -> None:
    """订阅 run:{run_id} 频道，逐条回调 handler（长连接，由调用方控制生命周期）。

    Args:
        on_ready: 订阅真正生效后的回调。调用方据此告知客户端「可以信任事件流」，
            避免订阅就绪前发布的事件丢失（WS 握手是异步的）。
    """
    client = _redis_client()
    pubsub = client.pubsub()
    try:
        await pubsub.subscribe(channel_for_run(run_id))
        if on_ready is not None:
            await on_ready()
        async for message in pubsub.listen():
            if message["type"] != "message":
                continue
            try:
                payload = json.loads(message["data"])
            except (TypeError, json.JSONDecodeError):
                continue
            await handler(payload)
    finally:
        await pubsub.unsubscribe(channel_for_run(run_id))
        # 只关订阅对象，不关共享连接池：它是进程级单例，其他请求还在用
        await pubsub.aclose()


async def subscribe_request_events(
    request_id: str,
    handler: EventHandler,
    *,
    on_ready: Callable[[], Awaitable[None]] | None = None,
) -> None:
    """订阅 request:{request_id} 频道（007）。语义与 run 频道一致。"""
    client = _redis_client()
    pubsub = client.pubsub()
    try:
        await pubsub.subscribe(channel_for_request(request_id))
        if on_ready is not None:
            await on_ready()
        async for message in pubsub.listen():
            if message["type"] != "message":
                continue
            try:
                payload = json.loads(message["data"])
            except (TypeError, json.JSONDecodeError):
                continue
            await handler(payload)
    finally:
        await pubsub.unsubscribe(channel_for_request(request_id))
        await pubsub.aclose()
