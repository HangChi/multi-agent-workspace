"""站内通知端点（T026，007）：列表 / 未读计数 / 批量已读。

每个端点都按 user_id = me 过滤——通知是个人收件箱数据，
「看到别人的通知」与「已读别人的通知」都不允许（N-3）。
"""

from fastapi import APIRouter, Query

from app.api.deps import DB, CurrentUser
from app.core.audit import NOTIFICATION_READ, write_audit
from app.schemas.notification import (
    MarkReadIn,
    NotificationList,
    NotificationOut,
    UnreadCountOut,
)
from app.services import notification_service

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("", response_model=NotificationList)
async def list_notifications(
    user: CurrentUser,
    db: DB,
    unread_only: bool = Query(default=False),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    """我的通知（倒序；unread_only 只取未读）。"""
    rows, total = await notification_service.list_notifications(
        db,
        user_id=user.id,
        unread_only=unread_only,
        page=page,
        page_size=page_size,
    )
    return {
        "items": [NotificationOut.model_validate(r) for r in rows],
        "total": total,
    }


@router.get("/unread-count", response_model=UnreadCountOut)
async def unread_count(user: CurrentUser, db: DB):
    """未读数（顶栏铃铛的轮询目标，refetchInterval ≤30s）。"""
    count = await notification_service.unread_count(db, user_id=user.id)
    return {"unread": count}


@router.post("/read")
async def mark_read(payload: MarkReadIn, user: CurrentUser, db: DB):
    """批量已读：ids 为空 = 全部已读。返回生效行数（幂等）。"""
    updated = await notification_service.mark_read(db, user_id=user.id, ids=payload.ids)
    # 审计只记条数不记 id 列表——通知已读是高频低危操作，逐条记会稀释审计流
    await write_audit(
        db,
        user_id=user.id,
        action=NOTIFICATION_READ,
        target_type="notification",
        target_id=user.id,
        detail={"updated": updated},
        strict=True,
    )
    await db.commit()
    return {"updated": updated}
