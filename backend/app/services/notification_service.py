"""站内通知服务（T026，007）：列表 / 未读计数 / 批量已读。

与分派服务的分工：dispatch_service 是**写**端（产生通知），本服务是**读/消费**
端。已读是个人读状态（read_at），通知内容本身不可变——「已读」不改变事实，
只改变「我见过没有」。
"""

from datetime import UTC, datetime

import structlog
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

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
