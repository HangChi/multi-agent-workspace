"""站内通知服务：写入（015 收敛）/ 列表 / 未读计数 / 批量已读。

写入收敛在这里之前，四处 `db.add(Notification(...))` 各写一遍（分派、项目成员
增删、执行完成、依赖解锁），字段各自拼、metrics 各自 inc。加实时推送时若还
散着，就要在四个地方各加一行 publish——漏一处就是「这条通知永远晚半分钟」。

推送时机：flush 之后、commit 之前。回滚场景下客户端可能先收到事件，但它收到
后做的是 invalidateQueries，重新拉一次就回到真实状态；「等 commit 再推」要把
每个端点改成两段式，改动面远大于收益。

已读是个人读状态（read_at），通知内容本身不可变——「已读」不改变事实，
只改变「我见过没有」。
"""

from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import metrics
from app.core.events import publish_user_notification
from app.models.notification import Notification

logger = structlog.get_logger(__name__)


async def list_notifications(
    db: AsyncSession,
    *,
    user_id: str,
    unread_only: bool = False,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[Notification], int]:
    """我的通知，按创建时间倒序（新的在上——与经手链的正序相反）。"""
    conditions = [Notification.user_id == user_id]
    if unread_only:
        conditions.append(Notification.read_at.is_(None))

    total = int(
        (
            await db.execute(
                select(func.count()).select_from(Notification).where(*conditions)
            )
        ).scalar_one()
    )
    stmt = (
        select(Notification)
        .where(*conditions)
        .order_by(Notification.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return list((await db.execute(stmt)).scalars().all()), total


async def unread_count(db: AsyncSession, *, user_id: str) -> int:
    """未读数（顶栏铃铛数据源；SC-007 的轮询目标）。"""
    return int(
        (
            await db.execute(
                select(func.count())
                .select_from(Notification)
                .where(
                    Notification.user_id == user_id,
                    Notification.read_at.is_(None),
                )
            )
        ).scalar_one()
    )


async def mark_read(
    db: AsyncSession, *, user_id: str, ids: list[str] | None = None
) -> int:
    """批量已读：ids 为空 = 全部已读。返回真实生效行数。

    - UPDATE 必须带 user_id 条件——否则拿到别人的通知 id 就能越权已读（N-3）；
    - 已读的再点一次不重复计数（WHERE read_at IS NULL 保证幂等）。
    """
    now = datetime.now(UTC)
    conditions = [
        Notification.user_id == user_id,
        Notification.read_at.is_(None),
    ]
    if ids is not None:
        if not ids:
            return 0
        conditions.append(Notification.id.in_(ids))
    result = await db.execute(
        update(Notification).where(*conditions).values(read_at=now)
    )
    # getattr 而不是 result.rowcount：asyncpg 下 UPDATE 的 Result 类型不稳，
    # mypy 会报 attr-defined（同 tasks/maintenance.py 的处理）
    return int(getattr(result, "rowcount", 0) or 0)


async def create_notification(
    db: AsyncSession,
    *,
    user_id: str,
    type_: str,
    title: str,
    request_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> Notification:
    """写一条站内通知并实时推给该用户（在线时）。"""
    notification = Notification(
        user_id=user_id,
        type=type_,
        request_id=request_id,
        title=title,
        payload=payload,
    )
    db.add(notification)
    await db.flush()
    metrics.NOTIFICATIONS_CREATED.inc(type=type_)
    await publish_user_notification(
        user_id,
        {
            "type": "notification",
            "notification_type": type_,
            "notification_id": notification.id,
            "request_id": request_id,
            "title": title,
        },
    )
    return notification
