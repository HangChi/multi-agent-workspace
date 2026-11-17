"""审计日志：write_audit 写入 audit_logs 表，同时输出 structlog。

审计是强制要求（宪法）：登录/注册/资源变更等关键操作必须落 audit_logs。
"""

import structlog
from sqlalchemy import exc
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.models.audit_log import AuditLog

logger = structlog.get_logger(__name__)

# ===== 定时调度（004）的审计动作 =====
# 集中定义而不散落在各调用点：动作名一旦写错，审计覆盖率就会静默下降
# （记录还在，但按月统计/排查时找不到对应事件）。004 要求「规则变更 +
# 每次触发」100% 可审计，因此这六个名字必须与 specs/004 的契约一致。
SCHEDULE_CREATE = "schedule_create"
SCHEDULE_UPDATE = "schedule_update"
SCHEDULE_DELETE = "schedule_delete"
SCHEDULE_ENABLE = "schedule_enable"
SCHEDULE_DISABLE = "schedule_disable"
SCHEDULE_TRIGGER = "schedule_trigger"

SCHEDULE_ACTIONS = frozenset(
    {
        SCHEDULE_CREATE,
        SCHEDULE_UPDATE,
        SCHEDULE_DELETE,
        SCHEDULE_ENABLE,
        SCHEDULE_DISABLE,
        SCHEDULE_TRIGGER,
    }
)


async def write_audit(
    db: AsyncSession,
    *,
    user_id: str,
    action: str,
    target_type: str | None = None,
    target_id: str | None = None,
    detail: dict | None = None,
    strict: bool = True,
) -> None:
    """写一条审计日志（只 flush，提交由调用方统一完成）。

    审计与业务共用同一个事务，所以审计写失败**必须**让整个请求失败：
    回滚事务后抛 `AppError`，端点返回 5xx，业务不会在「业务已提交、审计丢失」
    的中间态下静默落库（见 docs/code-audit-2026-11-17.md #9）。

    `strict=False` 仅留给「审计写在自己的独立事务里、丢了也不影响业务」的场景；
    当前无此类调用点，且与业务同事务时使用它会连业务一起丢，不要用。
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
    except exc.SQLAlchemyError as err:
        logger.exception(
            "audit_write_failed",
            user_id=user_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
        )
        # flush 失败后 session 已不可用，必须先回滚（连带业务），再决定是否上报
        await db.rollback()
        if strict:
            raise AppError("INTERNAL_ERROR", "审计日志写入失败") from err
