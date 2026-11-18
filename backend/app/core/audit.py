"""审计日志：write_audit 写入 audit_logs 表，同时输出 structlog。

审计是强制要求（宪法）：登录/注册/资源变更等关键操作必须落 audit_logs。
"""

import structlog
from sqlalchemy import exc
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.models.audit_log import AuditLog

logger = structlog.get_logger(__name__)

# ===== 运行自动重试（006）的审计动作 =====
# 与 005 的 agent_template_* 分开：自动重试是系统行为、手动重试（run_retry，001）
# 是用户行为。混在一起会让「用户重试了多少次」这个统计失真。
RUN_AUTO_RETRY = "run_auto_retry"

RUN_RETRY_ACTIONS = frozenset({RUN_AUTO_RETRY})

# ===== Agent 模板与克隆（005）的审计动作 =====
# 集中定义的理由同 004：动作名写错会让审计覆盖率静默下降。
# 005 要求「另存为模板 / 模板变更 / 模板删除 / 从模板创建 / 克隆」五类 100% 可审计。
AGENT_TEMPLATE_CREATE = "agent_template_create"
AGENT_TEMPLATE_UPDATE = "agent_template_update"
AGENT_TEMPLATE_DELETE = "agent_template_delete"
AGENT_CREATE_FROM_TEMPLATE = "agent_create_from_template"
AGENT_CLONE = "agent_clone"

AGENT_TEMPLATE_ACTIONS = frozenset(
    {
        AGENT_TEMPLATE_CREATE,
        AGENT_TEMPLATE_UPDATE,
        AGENT_TEMPLATE_DELETE,
        AGENT_CREATE_FROM_TEMPLATE,
        AGENT_CLONE,
    }
)

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

# ===== 需求分派（007）的审计动作 =====
# 与既有的 request_create / assign（内联在 requests.py）分开：分派类操作是
# 「责任转交」，assign 是「启动执行」，混在一起会让「谁经手过」的统计失真。
# 宪法：分派/接收/退回/转交 100% 可审计（FR-013 / SC-003）。
REQUEST_DISPATCH = "request_dispatch"
REQUEST_ACCEPT = "request_accept"
REQUEST_REJECT = "request_reject"
NOTIFICATION_READ = "notification_read"

REQUEST_DISPATCH_ACTIONS = frozenset(
    {
        REQUEST_DISPATCH,
        REQUEST_ACCEPT,
        REQUEST_REJECT,
        NOTIFICATION_READ,
    }
)

# ===== 项目实体（008）的审计动作 =====
# 删除动作的 detail 带 request_count（有多少条需求的归属被清空）——
# 「我的需求怎么集体没项目了」这类排查就靠这个数（contracts §1.4）。
PROJECT_CREATE = "project_create"
PROJECT_UPDATE = "project_update"
PROJECT_DELETE = "project_delete"

# ===== 需求依赖（009）的审计动作 =====
# detail 带对端标题快照——「这条边当初连的是哪条需求」不依赖目标还存在
# （边可删、需求可取消，快照是唯一留痕）。
REQUEST_DEPENDENCY_ADD = "request_dependency_add"
REQUEST_DEPENDENCY_REMOVE = "request_dependency_remove"

# ===== 项目成员（010）的审计动作 =====
# detail 带对方 user_id 与项目名——「谁被加进了哪个项目」事后可查。
PROJECT_MEMBER_ADD = "project_member_add"
PROJECT_MEMBER_REMOVE = "project_member_remove"


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
