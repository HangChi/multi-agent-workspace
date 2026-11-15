"""审计日志：write_audit 写入 audit_logs 表，同时输出 structlog。

审计是强制要求（宪法）：登录/注册/资源变更等关键操作必须落 audit_logs。
"""

import structlog
from sqlalchemy import exc
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog

logger = structlog.get_logger(__name__)


async def write_audit(
    db: AsyncSession,
    *,
    user_id: str,
    action: str,
    target_type: str | None = None,
    target_id: str | None = None,
    detail: dict | None = None,
    strict: bool = False,
) -> None:
    """写一条审计日志。

    默认失败回滚且不抛出；strict=True 时回滚后重新抛出，供强制原子审计的操作使用。
    """
    entry = AuditLog(
        user_id=user_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        detail=detail,
    )
    db.add(entry)
    try:
        await db.flush()
    except exc.SQLAlchemyError:
        logger.exception(
            "audit_write_failed",
            user_id=user_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
        )
        await db.rollback()
        if strict:
            raise
