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
) -> None:
    """写一条审计日志。

    失败不抛出（审计本身不应阻塞业务），但记录 error 级别日志便于排查。
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
