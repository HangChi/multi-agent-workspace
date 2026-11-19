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
import structlog

from app.core.redis import get_redis

logger = structlog.get_logger(__name__)

_run_event_prefix = "run:"
_request_event_prefix = "request:"
_user_event_prefix = "user:"


def channel_for_run(run_id: str) -> str:
    return f"{_run_event_prefix}{run_id}"


def channel_for_request(request_id: str) -> str:
    return f"{_request_event_prefix}{request_id}"


def channel_for_user(user_id: str) -> str:
    """用户级频道（015：站内通知实时推送）。

    与 run/request 频道的区别：那是「某个对象上的事件」，这是「发给我的事件」。
    前端只订阅自己这一条，与打开了多少个详情页无关。
    """
    return f"{_user_event_prefix}{user_id}:notifications"


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


async def publish_user_notification(user_id: str, payload: dict[str, Any]) -> None:
    """向 user:{id}:notifications 发布一条通知事件（015）。

    失败只记日志不抛：通知已落库，客户端轮询会补齐——不能让「推送通道抖动」
    变成「业务操作失败」。
    """
    try:
        await _redis_client().publish(
            channel_for_user(user_id), json.dumps(payload, ensure_ascii=False)
        )
    except Exception as exc:  # noqa: BLE001 - 见 docstring
        logger.warning(
            "publish_user_notification_failed",
            user_id=user_id[:8],
            error=type(exc).__name__,
        )


async def redis_ping() -> bool:
    """Redis 连通性探测（就绪检查用）。"""
    return bool(await _redis_client().ping())


EventHandler = Callable[[dict[str, Any]], Awaitable[None]]


async def _subscribe(channel: str, handler: EventHandler, on_ready) -> None:
    """通用订阅循环（run/request/user 三个频道共用一份实现）。

    合并原因：三份逐字重复的 listen 循环意味着「解析失败跳过」「finally 退订」
    这类细节要改三次，漏一次就是某个频道静默不工作。
    """
    client = _redis_client()
    pubsub = client.pubsub()
    try:
        await pubsub.subscribe(channel)
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
        await pubsub.unsubscribe(channel)
        # 只关订阅对象，不关共享连接池：它是进程级单例，其他请求还在用
        await pubsub.aclose()


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
    await _subscribe(channel_for_run(run_id), handler, on_ready)


async def subscribe_request_events(
    request_id: str,
    handler: EventHandler,
    *,
    on_ready: Callable[[], Awaitable[None]] | None = None,
) -> None:
    """订阅 request:{request_id} 频道（007）。语义与 run 频道一致。"""
    await _subscribe(channel_for_request(request_id), handler, on_ready)


async def subscribe_user_notifications(
    user_id: str,
    handler: EventHandler,
    *,
    on_ready: Callable[[], Awaitable[None]] | None = None,
) -> None:
    """订阅 user:{id}:notifications 频道（015）。语义与 run 频道一致。"""
    await _subscribe(channel_for_user(user_id), handler, on_ready)
