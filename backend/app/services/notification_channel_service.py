"""外部通知通道服务（018）：通道 CRUD + 投递记录 + 单次投递。

三条纪律：

1. **URL 校验复用 `core.net_guard`**，与 002 的 `read_url` 同一套判定。
   两处各写一份必分叉，而「LLM 工具挡了内网、用户配的 webhook 没挡」是最危险的
   那种分叉（research R-02）。保存时校验一次，投递前再校验一次（DNS 可能已变）。
2. **投递是 at-least-once**（R-04）：超时后无法确定对方是否收到，重试可能重复。
   头部带 `X-AW-Delivery` 让接收端自己幂等。
3. **4xx 不重试**（R-05）：URL 错了/密钥不匹配，重试只是把同一个错误重复三遍。

`enqueue_for_notification` 在通知落库后为每条订阅通道建 delivery 并投递；
`attempt_delivery` 只负责**一次**尝试，重试由 Celery 任务按退避调度
（不在这里 sleep 占住 worker）。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.core.config import settings
from app.core.net_guard import (
    NetGuardError,
    is_metadata_address,
    normalize_http_url,
    resolve_hosts,
    resolve_public_hosts,
)
from app.models.notification import Notification
from app.models.notification_channel import (
    DELIVERY_FAILED,
    DELIVERY_PENDING,
    DELIVERY_SUCCEEDED,
    NotificationChannel,
    NotificationDelivery,
)

logger = structlog.get_logger(__name__)

# 可订阅的事件类型：与 notification_type 枚举一致（新增类型要同步这里）。
SUBSCRIBABLE_EVENTS: tuple[str, ...] = (
    "request_dispatched",
    "request_accepted",
    "request_rejected",
    "request_reassigned",
    "run_completed",
    "dependency_ready",
    "project_member_added",
    "project_member_removed",
    "project_ownership_received",
)

_MAX_ATTEMPTS = 3
_TIMEOUT_SECONDS = 10.0
_TEST_TIMEOUT_SECONDS = 8.0
_RETRY_BACKOFF_SECONDS = (2, 8, 30)


# ---------------------------------------------------------------- 通道 CRUD


async def list_channels(db: AsyncSession, user_id: str) -> list[NotificationChannel]:
    return list(
        (
            await db.execute(
                select(NotificationChannel)
                .where(NotificationChannel.user_id == user_id)
                .order_by(NotificationChannel.created_at)
            )
        )
        .scalars()
        .all()
    )


async def _load_owned_channel(
    db: AsyncSession, user_id: str, channel_id: str
) -> NotificationChannel:
    """渠道是单人资源：不是自己的 404（不暴露存在性，016 口径）。"""
    channel = await db.get(NotificationChannel, channel_id)
    if channel is None or channel.user_id != user_id:
        raise AppError("NOT_FOUND", "通知通道不存在")
    return channel


async def _target_error(url: str) -> str | None:
    """目标地址是否允许（返回错误文案，None = 放行）。

    默认只放行公网。`notification_allow_private_hosts` 打开时（纯内网部署，
    接收端就是内网网关）放行私网，但**仍然拦云元数据地址**——那个没有业务用途，
    只有「取机器临时凭证」这一种用法（research R-02）。
    """
    host = _host_of(url)
    if settings.notification_allow_private_hosts:
        try:
            ips = await resolve_hosts(host)
        except NetGuardError as exc:
            return str(exc)
        if any(is_metadata_address(ip) for ip in ips):
            return "不允许投递到云元数据地址"
        return None
    try:
        await resolve_public_hosts(host)
    except NetGuardError as exc:
        return str(exc)
    return None


async def _validate_url(url: str) -> str:
    """规范化 + 解析后校验。返回规范化后的 URL。

    保存时做这一层，是为了让用户**当场**拿到 400（而不是投递时才发现配错了）。
    """
    try:
        normalized = normalize_http_url(url)
    except NetGuardError as exc:
        raise AppError("INVALID_INPUT", f"该地址不允许使用：{exc}") from exc
    error = await _target_error(normalized)
    if error:
        raise AppError("INVALID_INPUT", f"该地址不允许使用：{error}")
    return normalized


def _host_of(url: str) -> str:
    return urlparse(url).hostname or ""


def _validate_events(event_types: list[str]) -> list[str]:
    unknown = [e for e in event_types if e not in SUBSCRIBABLE_EVENTS]
    if unknown:
        raise AppError(
            "INVALID_INPUT",
            f"未知事件类型：{', '.join(unknown[:5])}（可选：{', '.join(SUBSCRIBABLE_EVENTS)}）",
        )
    # 去重同时保留顺序
    return list(dict.fromkeys(event_types))


async def create_channel(
    db: AsyncSession,
    *,
    user_id: str,
    url: str,
    event_types: list[str],
    enabled: bool = True,
) -> NotificationChannel:
    channel = NotificationChannel(
        user_id=user_id,
        kind="webhook",
        url=await _validate_url(url),
        secret=secrets.token_urlsafe(32)[:64],
        event_types=_validate_events(event_types),
        enabled=enabled,
    )
    db.add(channel)
    await db.flush()
    logger.info("notification_channel_created", channel_id=channel.id, user_id=user_id)
    return channel


async def update_channel(
    db: AsyncSession,
    *,
    user_id: str,
    channel_id: str,
    url: str | None = None,
    event_types: list[str] | None = None,
    enabled: bool | None = None,
) -> NotificationChannel:
    channel = await _load_owned_channel(db, user_id, channel_id)
    if url is not None:
        channel.url = await _validate_url(url)
    if event_types is not None:
        channel.event_types = _validate_events(event_types)
    if enabled is not None:
        channel.enabled = enabled
    await db.flush()
    return channel


async def delete_channel(db: AsyncSession, *, user_id: str, channel_id: str) -> None:
    channel = await _load_owned_channel(db, user_id, channel_id)
    await db.delete(channel)
    await db.flush()


async def reset_secret(
    db: AsyncSession, *, user_id: str, channel_id: str
) -> NotificationChannel:
    channel = await _load_owned_channel(db, user_id, channel_id)
    channel.secret = secrets.token_urlsafe(32)[:64]
    await db.flush()
    return channel


async def list_deliveries(
    db: AsyncSession, *, user_id: str, channel_id: str, limit: int = 20
) -> tuple[list[NotificationDelivery], NotificationChannel]:
    channel = await _load_owned_channel(db, user_id, channel_id)
    rows = list(
        (
            await db.execute(
                select(NotificationDelivery)
                .where(NotificationDelivery.channel_id == channel_id)
                .order_by(NotificationDelivery.created_at.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return rows, channel


# ---------------------------------------------------------------- 投递


def sign(secret: str, timestamp: str, body: bytes) -> str:
    """`sha256=HMAC_SHA256(secret, f"{timestamp}.{body}")`（contracts §3）。

    带 timestamp 是为了让接收端能拒绝重放（超过 5 分钟的请求可以丢弃）。
    """
    message = timestamp.encode() + b"." + body
    return "sha256=" + hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def _payload(notification: Notification, delivery_id: str) -> dict[str, Any]:
    return {
        "delivery_id": delivery_id,
        "notification_id": notification.id,
        "event": notification.type,
        "title": notification.title,
        "request_id": notification.request_id,
        "occurred_at": notification.created_at.isoformat()
        if notification.created_at
        else datetime.now(UTC).isoformat(),
        "payload": notification.payload,
    }


def _matches(channel: NotificationChannel, event_type: str) -> bool:
    """空订阅列表 = 全部事件（新事件类型自动收到）。"""
    return not channel.event_types or event_type in channel.event_types


async def enqueue_for_notification(
    db: AsyncSession, notification: Notification
) -> list[str]:
    """为「启用 + 订阅了该事件」的通道各建一条 delivery，返回 delivery id 列表。

    只建记录，不在这里投递：调用方（`create_notification`）会在事务内调用本函数，
    真正的 HTTP 请求交给 Celery（FR-005：不阻塞业务事务）。
    """
    channels = list(
        (
            await db.execute(
                select(NotificationChannel).where(
                    NotificationChannel.user_id == notification.user_id,
                    NotificationChannel.enabled.is_(True),
                )
            )
        )
        .scalars()
        .all()
    )
    delivery_ids: list[str] = []
    for channel in channels:
        if not _matches(channel, notification.type):
            continue
        delivery = NotificationDelivery(
            channel_id=channel.id,
            notification_id=notification.id,
            event_type=notification.type,
            status=DELIVERY_PENDING,
            attempts=0,
        )
        db.add(delivery)
        await db.flush()
        delivery_ids.append(delivery.id)
    return delivery_ids


class DeliveryOutcome:
    """一次尝试的结果：`retry` 交给 Celery 按退避重排，其余为终态。"""

    RETRY = "retry"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    MISSING = "missing"


async def attempt_delivery(
    db: AsyncSession,
    delivery_id: str,
    client: httpx.AsyncClient | None = None,
) -> tuple[str, int]:
    """执行一次投递尝试。返回 `(outcome, retry_after_seconds)`。

    状态落库（attempts/last_error/status）在调用方 commit 时生效。
    """
    delivery = await db.get(NotificationDelivery, delivery_id)
    if delivery is None:
        # 事务尚未提交（enqueue 与 commit 之间入队）→ 让任务稍后再来一次
        return DeliveryOutcome.MISSING, 5
    channel = await db.get(NotificationChannel, delivery.channel_id)
    if channel is None or not channel.enabled:
        # 通道在排队期间被删/禁用：丢弃，不补发（FR-008）
        logger.info(
            "notification_delivery_dropped",
            delivery_id=delivery_id,
            reason="channel 不存在或已禁用",
        )
        delivery.status = DELIVERY_FAILED
        delivery.last_error = "通道已删除或禁用，投递取消"
        return DeliveryOutcome.FAILED, 0

    notification = (
        await db.get(Notification, delivery.notification_id)
        if delivery.notification_id
        else None
    )
    if notification is None:
        delivery.status = DELIVERY_FAILED
        delivery.last_error = "通知已不存在，投递取消"
        return DeliveryOutcome.FAILED, 0

    body_payload = _payload(notification, delivery.id)
    body = json.dumps(body_payload, ensure_ascii=False, separators=(",", ":")).encode()
    timestamp = str(int(datetime.now(UTC).timestamp()))

    # 投递前再校验一次：URL 可能是几天前保存的，DNS 早已变化（R-02）
    target_error = await _target_error(channel.url)
    if target_error:
        delivery.status = DELIVERY_FAILED
        delivery.last_error = f"目标地址校验失败：{target_error}"[:500]
        delivery.attempts += 1
        delivery.last_attempt_at = datetime.now(UTC)
        return DeliveryOutcome.FAILED, 0

    delivery.attempts += 1
    delivery.last_attempt_at = datetime.now(UTC)

    status_code: int | None = None
    error: str | None = None
    # client 可注入：集成测试用 MockTransport 验重试/状态码分支，不打真实网络
    owns_client = client is None
    if client is None:
        # 不跟随重定向：302 到内网是绕开保存期校验最直接的一条路（R-02）
        client = httpx.AsyncClient(timeout=_TIMEOUT_SECONDS, follow_redirects=False)
    try:
        resp = await client.post(
            channel.url,
            content=body,
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "User-Agent": "AgentWorkbench-Webhook/1.0",
                "X-AW-Event": delivery.event_type,
                "X-AW-Delivery": delivery.id,
                "X-AW-Timestamp": timestamp,
                "X-AW-Signature": sign(channel.secret, timestamp, body),
            },
        )
        status_code = resp.status_code
        if 200 <= status_code < 300:
            delivery.status = DELIVERY_SUCCEEDED
            delivery.last_error = None
            logger.info(
                "notification_delivered",
                delivery_id=delivery.id,
                channel_id=channel.id,
                status_code=status_code,
            )
            return DeliveryOutcome.SUCCEEDED, 0
        # 摘要不含响应体（可能含对方敏感信息），只留状态码
        error = f"HTTP {status_code}"
    except httpx.TimeoutException:
        error = "请求超时"
    except httpx.HTTPError as exc:
        error = f"{type(exc).__name__}: {exc}"[:200]
    finally:
        if owns_client:
            await client.aclose()

    delivery.last_error = error[:500]
    retryable = status_code is None or status_code == 429 or status_code >= 500
    if retryable and delivery.attempts < _MAX_ATTEMPTS:
        delivery.status = DELIVERY_PENDING
        backoff = _RETRY_BACKOFF_SECONDS[
            min(delivery.attempts - 1, len(_RETRY_BACKOFF_SECONDS) - 1)
        ]
        logger.warning(
            "notification_delivery_retry",
            delivery_id=delivery.id,
            attempts=delivery.attempts,
            error=error,
            backoff=backoff,
        )
        return DeliveryOutcome.RETRY, backoff

    delivery.status = DELIVERY_FAILED
    logger.warning(
        "notification_delivery_failed",
        delivery_id=delivery.id,
        attempts=delivery.attempts,
        error=error,
    )
    return DeliveryOutcome.FAILED, 0


async def send_test(
    db: AsyncSession,
    *,
    user_id: str,
    channel_id: str,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """同步发一条测试事件（FR-009）：配置时就能确认真实弹道。

    不落投递记录：这是「试一下」，不是业务事件；失败原因直接回给前端。
    """
    channel = await _load_owned_channel(db, user_id, channel_id)
    now = datetime.now(UTC).isoformat()
    # 先取成局部变量：直接写 body_payload["delivery_id"] 会让 dict 的值类型变成
    # `str | None | dict`，headers 就不符合 httpx 的 Mapping[str, str]
    delivery_id = f"test-{secrets.token_hex(6)}"
    body_payload = {
        "delivery_id": delivery_id,
        "notification_id": None,
        "event": "test",
        "title": "Agent 工作台测试通知",
        "request_id": None,
        "occurred_at": now,
        "payload": {"message": "如果你看到这条消息，说明通道配置正确。"},
    }
    body = json.dumps(body_payload, ensure_ascii=False, separators=(",", ":")).encode()
    timestamp = str(int(datetime.now(UTC).timestamp()))
    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(
            timeout=_TEST_TIMEOUT_SECONDS, follow_redirects=False
        )
    try:
        resp = await client.post(
            channel.url,
            content=body,
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "User-Agent": "AgentWorkbench-Webhook/1.0",
                "X-AW-Event": "test",
                "X-AW-Delivery": delivery_id,
                "X-AW-Timestamp": timestamp,
                "X-AW-Signature": sign(channel.secret, timestamp, body),
            },
        )
        ok = 200 <= resp.status_code < 300
        return {
            "ok": ok,
            "status_code": resp.status_code,
            "error": None if ok else f"HTTP {resp.status_code}",
        }
    except httpx.TimeoutException:
        return {"ok": False, "status_code": None, "error": "请求超时"}
    except httpx.HTTPError as exc:
        return {
            "ok": False,
            "status_code": None,
            "error": f"{type(exc).__name__}: {exc}"[:200],
        }
    finally:
        if owns_client:
            await client.aclose()
