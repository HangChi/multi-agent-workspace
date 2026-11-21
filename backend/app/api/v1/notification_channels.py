"""外部通知通道端点（018）：挂在 `/notification-channels`。

权限：所有端点都要 member/admin（viewer 走 `MemberUser` 依赖 → 403）。
归属：渠道是单人资源，别人的一律 404（016 口径，不暴露存在性）。

为什么 `test` 是同步等待一次结果而不是排队：配置时人要立刻知道「弹道通不通」，
排队的测试发出去失败了他也看不见（FR-009）。代价是最多阻塞 8 秒，接受。
"""

from fastapi import APIRouter, Query, status
from sqlalchemy import select

from app.api.deps import DB, CurrentUser, MemberUser
from app.core.audit import NOTIFICATION_CHANNEL_CREATE, write_audit
from app.models.notification_channel import NotificationChannel
from app.schemas.notification_channel import (
    ChannelTestResult,
    EventTypeList,
    NotificationChannelCreate,
    NotificationChannelList,
    NotificationChannelOut,
    NotificationChannelUpdate,
    NotificationDeliveryList,
    NotificationDeliveryOut,
)
from app.services import notification_channel_service

router = APIRouter(prefix="/notification-channels", tags=["notification-channels"])


def _out(channel) -> NotificationChannelOut:
    return NotificationChannelOut.model_validate(channel)


async def _reload(db, channel_id: str) -> NotificationChannel:
    """commit 后重新读一次再序列化。

    会话是 `expire_on_commit=True`：commit 之后直接 model_validate 会触发属性
    惰性加载 → `MissingGreenlet`。`populate_existing` 保证读回的是新值，
    而不是身份映射里的旧对象（同 project_service._load_visible_project 的理由）。
    """
    row = (
        (
            await db.execute(
                select(NotificationChannel)
                .where(NotificationChannel.id == channel_id)
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .first()
    )
    assert row is not None
    return row


# 事件类型的中文标签：与 SUBSCRIBABLE_EVENTS 同源，前端不再维护一份映射
_EVENT_LABELS = {
    "request_dispatched": "需求派给我",
    "request_accepted": "接手人已接收",
    "request_rejected": "接手人已拒绝",
    "request_reassigned": "需求被转交",
    "run_completed": "运行结束",
    "dependency_ready": "前置完成、可以开工",
    "project_member_added": "被加入项目",
    "project_member_removed": "被移出项目",
    "project_ownership_received": "成为项目创建者",
    "invite_request_approved": "邀请码申请已通过",
    "invite_request_rejected": "邀请码申请未通过",
}


@router.get("/event-types", response_model=EventTypeList)
async def list_event_types(_m: MemberUser):
    """可订阅的事件类型（空订阅 = 全部，前端也要把这个语义说清）。"""
    return {
        "items": [
            {"value": value, "label": _EVENT_LABELS.get(value, value)}
            for value in notification_channel_service.SUBSCRIBABLE_EVENTS
        ]
    }


@router.get("", response_model=NotificationChannelList)
async def list_channels(user: CurrentUser, _m: MemberUser, db: DB):
    rows = await notification_channel_service.list_channels(db, user.id)
    return {"items": [_out(c) for c in rows], "total": len(rows)}


@router.post(
    "", response_model=NotificationChannelOut, status_code=status.HTTP_201_CREATED
)
async def create_channel(
    payload: NotificationChannelCreate, user: CurrentUser, _m: MemberUser, db: DB
):
    """建通道。URL 在内网/保留段会 400（保存时就拒，不是投递时才拒）。"""
    channel = await notification_channel_service.create_channel(
        db,
        user_id=user.id,
        url=payload.url,
        event_types=payload.event_types,
        enabled=payload.enabled,
    )
    await write_audit(
        db,
        user_id=user.id,
        action=NOTIFICATION_CHANNEL_CREATE,
        target_type="notification_channel",
        target_id=channel.id,
        # 记 URL 的 host 与事件订阅即可：完整 URL 可能带 token
        detail={"kind": channel.kind, "url": channel.url[:200]},
        strict=True,
    )
    await db.commit()
    return _out(await _reload(db, channel.id))


@router.patch("/{channel_id}", response_model=NotificationChannelOut)
async def update_channel(
    channel_id: str,
    payload: NotificationChannelUpdate,
    user: CurrentUser,
    _m: MemberUser,
    db: DB,
):
    channel = await notification_channel_service.update_channel(
        db,
        user_id=user.id,
        channel_id=channel_id,
        url=payload.url,
        event_types=payload.event_types,
        enabled=payload.enabled,
    )
    await db.commit()
    return _out(await _reload(db, channel.id))


@router.delete("/{channel_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_channel(channel_id: str, user: CurrentUser, _m: MemberUser, db: DB):
    await notification_channel_service.delete_channel(
        db, user_id=user.id, channel_id=channel_id
    )
    await db.commit()


@router.post("/{channel_id}/test", response_model=ChannelTestResult)
async def test_channel(channel_id: str, user: CurrentUser, _m: MemberUser, db: DB):
    """同步发一条测试事件（最多等 8 秒）。"""
    result = await notification_channel_service.send_test(
        db, user_id=user.id, channel_id=channel_id
    )
    return ChannelTestResult(**result)


@router.post("/{channel_id}/reset-secret", response_model=NotificationChannelOut)
async def reset_secret(channel_id: str, user: CurrentUser, _m: MemberUser, db: DB):
    channel = await notification_channel_service.reset_secret(
        db, user_id=user.id, channel_id=channel_id
    )
    await db.commit()
    return _out(await _reload(db, channel.id))


@router.get("/{channel_id}/deliveries", response_model=NotificationDeliveryList)
async def list_deliveries(
    channel_id: str,
    user: CurrentUser,
    _m: MemberUser,
    db: DB,
    limit: int = Query(default=20, ge=1, le=100),
):
    """最近投递（设置页「配好了没收到」的排查入口）。"""
    rows, _channel = await notification_channel_service.list_deliveries(
        db, user_id=user.id, channel_id=channel_id, limit=limit
    )
    return {
        "items": [NotificationDeliveryOut.model_validate(r) for r in rows],
        "total": len(rows),
    }
