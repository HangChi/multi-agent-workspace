"""事件总线：Redis Pub/Sub 封装。

桥接 Worker → API → WebSocket 的实时事件链路（对齐 contracts/api.md 的事件流）：
- 发布端（Worker/编排）：publish_run_event(run_id, payload)
- 订阅端（API/WS）：subscribe_run_events(run_id, handler)

频道命名：run:{run_id}，payload 为 JSON 字符串。
"""

import json
from collections.abc import Awaitable, Callable
from typing import Any

import redis.asyncio as aioredis

from app.core.config import settings

_run_event_prefix = "run:"


def channel_for_run(run_id: str) -> str:
    return f"{_run_event_prefix}{run_id}"


def _redis_client() -> aioredis.Redis:
    return aioredis.from_url(settings.redis_url, decode_responses=True)


async def publish_run_event(run_id: str, payload: dict[str, Any]) -> None:
    """向 run:{run_id} 频道发布一条事件。"""
    client = _redis_client()
    try:
        await client.publish(channel_for_run(run_id), json.dumps(payload, ensure_ascii=False))
    finally:
        await client.aclose()


EventHandler = Callable[[dict[str, Any]], Awaitable[None]]


async def subscribe_run_events(run_id: str, handler: EventHandler) -> None:
    """订阅 run:{run_id} 频道，逐条回调 handler（长连接，由调用方控制生命周期）。"""
    client = _redis_client()
    pubsub = client.pubsub()
    try:
        await pubsub.subscribe(channel_for_run(run_id))
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
        await client.aclose()
