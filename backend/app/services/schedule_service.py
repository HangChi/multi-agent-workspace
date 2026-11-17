"""调度规则服务（T017，US1）：CRUD + 六步校验 + next_run_at 重算。

三段各自的责任边界：

1. **校验顺序**是契约（contracts/api.md §1）。顺序决定用户先看到哪个错误，
   把目标校验提到 cron 之前会让用户改完目标才发现表达式也是错的。
2. **`next_run_at` 何时重算**是语义。改 cron/时区/启用态要重算，
   改模板/目标不重算——时间语义没变，重算只会让用户看到莫名的跳动。
3. **越权与不存在同响应**。用 403 区分"存在但无权"会让任何人可枚举
   他人的调度 id（403 = 存在，404 = 不存在）。
"""

from datetime import UTC, datetime

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.models.agent import Agent
from app.models.group import AgentGroup
from app.models.schedule import Schedule
from app.models.user import User
from app.schemas.schedule import (
    ScheduleCreate,
    ScheduleOut,
    ScheduleTargetOut,
    ScheduleTargetType,
    ScheduleTemplate,
    ScheduleUpdate,
    TargetStatus,
)
from app.services.schedule_cron import (
    CronExpressionError,
    next_after,
    parse_cron,
    validate_cron,
)

logger = structlog.get_logger(__name__)

# 创建者已停用时触发会被记为 failed（契约第 5 步），因此这里也要能读到用户状态。


def _field_error(field: str, message: str, *, code: str = "INVALID_INPUT") -> AppError:
    """带字段定位的校验错误（422）。

    `detail.field` 让前端能把错误挂到具体输入框，而不是只弹一条笼统提示。
    用 422 而不是 400：契约 §1 第 3/4 步明确要求 `422 INVALID_INPUT` +
    `detail.field`，语义是「这个字段的取值不合法」（区别于第 6 步的
    400「两个合法值组合不相容」）。
    """
    return AppError(code, message, detail={"field": field}, status_code=422)


def _check_cron(expr: str, timezone: str, *, now: datetime | None = None) -> datetime:
    """契约第 3、4 步：表达式可解析，且**能算出下一时刻**。

    判据必须是"能算出"而不是"能解析"：`0 0 31 2 *` 可解析但永不发生，
    放它进去等于建了一条永远不会跑的规则，而界面上看起来一切正常。

    Returns:
        下一触发时刻（带时区）。
    """
    now = now or datetime.now(UTC)
    # 先单独校验时区，好让错误指向 timezone 而不是笼统的 cron
    message = validate_cron(expr, timezone, now)
    if message is None:
        try:
            return next_after(parse_cron(expr, timezone), now)
        except CronExpressionError as exc:  # pragma: no cover - validate 已覆盖
            message = str(exc)

    # validate_cron 不区分"表达式问题"与"时区问题"，这里按语义拆开：
    # 时区未知时不该说"表达式不合法"，否则用户会去改表达式。
    if "时区" in message:
        raise _field_error("timezone", message)
    raise _field_error("cron_expr", message)


def _check_target_type_strategy(target_type: str, strategy: str | None) -> None:
    """契约第 6 步：strategy 只对组有意义。

    用 400 而非 422：两个值各自都合法，是组合不相容，不是字段不合法。
    """
    if target_type == ScheduleTargetType.agent.value and strategy:
        raise AppError(
            "INVALID_INPUT",
            "单个 Agent 不能指定路由策略，请改为 group 或去掉 strategy",
            detail={"field": "target.strategy"},
        )


async def resolve_target(
    db: AsyncSession,
    *,
    target_type: str,
    target_id: str,
    user_id: str,
    require_active: bool = True,
) -> tuple[str, str]:
    """契约第 5 步：目标存在、属于调用者、未归档。

    三种情况统一 404「Agent 或组不存在」——对当前用户而言，别人的 Agent
    与不存在的 Agent 是同一件事，用 403 区分同样可枚举。

    Returns:
        (展示名, 状态值)。目标可用时一并把名字取出，避免上层再查一次。
    """
    # 两个模型分开查而不是拼一个 Union：`Agent` 与 `AgentGroup` 各自都有
    # owner_id/name/status，但类型检查器无法从联合类型上推断出这些属性。
    target: Agent | AgentGroup | None
    if target_type == ScheduleTargetType.agent.value:
        target = await db.get(Agent, target_id)
    else:
        target = await db.get(AgentGroup, target_id)

    if target is None or target.owner_id != user_id:
        raise AppError("NOT_FOUND", "Agent 或组不存在", detail={"field": "target.id"})
    if require_active and target.status != "active":
        raise AppError("NOT_FOUND", "Agent 或组不存在", detail={"field": "target.id"})
    return target.name, target.status


async def _require_owner_view(
    db: AsyncSession, schedule_id: str, user: User
) -> Schedule:
    """按权限取一条规则：member 仅自己，admin 任意读。

    不存在与无权限**同响应 404**：admin 之外的他人调度对当前用户
    就是"不存在"。
    """
    schedule = await db.get(Schedule, schedule_id)
    if schedule is None:
        raise AppError("NOT_FOUND", "调度不存在")
    if user.role != "admin" and schedule.owner_id != user.id:
        raise AppError("NOT_FOUND", "调度不存在")
    return schedule


async def _require_owner_write(
    db: AsyncSession, schedule_id: str, user: User
) -> Schedule:
    """按权限取一条规则用于修改：仅本人。

    admin 也**不能改他人**（FR-005）：admin 的职责是排障，不是替用户调度
    生产任务。

    这里与读路径的差别是关键：读路径对他人一律 404（避免有人用 403/404
    差异枚举他人调度 id）。但 admin 本来就能读到任何调度，403 不携带任何
    新信息，反而能让 admin 分清「这条不存在」和「这条存在但我不该动」。
    因此顺序是：不存在 → 404；存在但非本人且是 admin → 403；
    存在但非本人且是 member → 404。
    """
    schedule = await db.get(Schedule, schedule_id)
    if schedule is None:
        raise AppError("NOT_FOUND", "调度不存在")
    if schedule.owner_id != user.id:
        if user.role == "admin":
            raise AppError("FORBIDDEN", "管理员只能查看他人的调度，不能修改或删除")
        raise AppError("NOT_FOUND", "调度不存在")
    return schedule


async def _load_target_display(
    db: AsyncSession, schedule: Schedule
) -> tuple[str | None, TargetStatus]:
    """实时取目标的展示名与状态（不缓存）。

    `missing` 与 `archived` 必须分开：归档可恢复，物理删除不可恢复，
    界面需要据此提示"目标已不存在，请重建规则"。
    """
    target: Agent | AgentGroup | None
    if schedule.target_type == ScheduleTargetType.agent.value:
        target = await db.get(Agent, schedule.target_id)
    else:
        target = await db.get(AgentGroup, schedule.target_id)
    if target is None:
        return None, TargetStatus.missing
    return target.name, (
        TargetStatus.active if target.status == "active" else TargetStatus.archived
    )


async def _to_out(db: AsyncSession, schedule: Schedule, user: User) -> ScheduleOut:
    """组装输出。admin 视角填 owner_email，member 看自己时留 null。"""
    name, status = await _load_target_display(db, schedule)

    owner_email = None
    if user.role == "admin" and schedule.owner_id != user.id:
        owner = await db.get(User, schedule.owner_id)
        owner_email = owner.email if owner else None

    return ScheduleOut(
        id=schedule.id,
        owner_id=schedule.owner_id,
        owner_email=owner_email,
        name=schedule.name,
        description=schedule.description,
        cron_expr=schedule.cron_expr,
        timezone=schedule.timezone,
        template=ScheduleTemplate(
            title=schedule.template_title,
            description=schedule.template_description,
            expected_output=schedule.template_expected_output,
            priority=schedule.template_priority,
        ),
        target=ScheduleTargetOut(
            type=ScheduleTargetType(schedule.target_type),
            id=schedule.target_id,
            strategy=schedule.target_strategy,
            name=name,
            status=status,
        ),
        enabled=schedule.enabled,
        next_run_at=schedule.next_run_at,
        last_run_at=schedule.last_run_at,
        last_result=schedule.last_result,
        consecutive_failures=schedule.consecutive_failures,
        auto_disabled_reason=schedule.auto_disabled_reason,
        created_at=schedule.created_at,
        updated_at=schedule.updated_at,
    )


async def create_schedule(
    db: AsyncSession, *, user: User, payload: ScheduleCreate
) -> ScheduleOut:
    """创建规则。校验按契约六步顺序执行（角色已由路由层保证）。"""
    # 第 3、4 步先算：cron 错就立即返回，不浪费一次目标查询，
    # 也保证同时错两处时用户先看到 cron 的错误。
    next_run_at = _check_cron(payload.cron_expr, payload.timezone)
    _check_target_type_strategy(payload.target.type.value, payload.target.strategy)
    # 第 5 步
    await resolve_target(
        db,
        target_type=payload.target.type.value,
        target_id=payload.target.id,
        user_id=user.id,
    )

    schedule = Schedule(
        owner_id=user.id,
        name=payload.name,
        description=payload.description,
        cron_expr=payload.cron_expr,
        timezone=payload.timezone,
        template_title=payload.template.title,
        template_description=payload.template.description,
        template_expected_output=payload.template.expected_output,
        template_priority=payload.template.priority.value,
        target_type=payload.target.type.value,
        target_id=payload.target.id,
        target_strategy=payload.target.strategy,
        enabled=payload.enabled,
        # 停用态不留 next_run_at：CHECK 约束允许，但留着一个"未来的时间"
        # 会让列表页显示"下次触发：明天 9 点"，而实际不会触发。
        next_run_at=next_run_at if payload.enabled else None,
    )
    db.add(schedule)
    await db.flush()
    # 同 update_schedule 的理由：created_at/updated_at 是服务端默认值，
    # flush 后为过期状态，直接访问会触发 greenlet 外的懒加载。
    await db.refresh(schedule)
    logger.info(
        "schedule_created",
        schedule_id=schedule.id,
        owner_id=user.id,
        cron=schedule.cron_expr,
        next_run_at=schedule.next_run_at.isoformat() if schedule.next_run_at else None,
    )
    return await _to_out(db, schedule, user)


async def update_schedule(
    db: AsyncSession, *, user: User, schedule_id: str, payload: ScheduleUpdate
) -> ScheduleOut:
    """部分更新。

    `changes()` 只含用户真正传了的字段；没传的一律不动。
    """
    schedule = await _require_owner_write(db, schedule_id, user)
    changes = payload.changes()

    # 时间语义是否变化 —— 只有这四项会触发 next_run_at 重算
    time_semantics_changed = False

    if "cron_expr" in changes and changes["cron_expr"] is not None:
        schedule.cron_expr = changes["cron_expr"]
        time_semantics_changed = True
    if "timezone" in changes and changes["timezone"] is not None:
        schedule.timezone = changes["timezone"]
        time_semantics_changed = True
    if "name" in changes and changes["name"] is not None:
        schedule.name = changes["name"]
    if "description" in changes:
        # 空字符串表示清空（与 null=不改 相对）
        schedule.description = changes["description"] or None

    if "template" in changes and changes["template"] is not None:
        tpl = payload.template
        tpl_changes = tpl.changes() if tpl is not None else {}
        if "title" in tpl_changes and tpl_changes["title"] is not None:
            schedule.template_title = tpl_changes["title"]
        if "description" in tpl_changes:
            schedule.template_description = tpl_changes["description"] or None
        if "expected_output" in tpl_changes:
            schedule.template_expected_output = tpl_changes["expected_output"] or None
        if "priority" in tpl_changes and tpl_changes["priority"] is not None:
            schedule.template_priority = tpl_changes["priority"]

    if "target" in changes and changes["target"] is not None:
        target = payload.target
        assert target is not None  # changes() 已确保非 None
        _check_target_type_strategy(target.type.value, target.strategy)
        # 第 5 步每次都做：目标可能在这期间被归档或转给别人
        await resolve_target(
            db,
            target_type=target.type.value,
            target_id=target.id,
            user_id=user.id,
        )
        schedule.target_type = target.type.value
        schedule.target_id = target.id
        schedule.target_strategy = target.strategy

    if "enabled" in changes and changes["enabled"] is not None:
        enabled = changes["enabled"]
        if enabled != schedule.enabled:
            schedule.enabled = enabled
            if not enabled:
                schedule.next_run_at = None
                # 手动停用清掉自动停用原因：那条原因描述的是"上次为什么被系统停"，
                # 用户已经主动关了，再留着会让界面显示一段过期的解释。
                schedule.auto_disabled_reason = None
            else:
                time_semantics_changed = True

    # 重算：从现在算，**绝不从旧的 next_run_at 继续**。
    # 后者会让一个停了很久的规则在启用瞬间补跑一次。
    if time_semantics_changed and schedule.enabled:
        schedule.next_run_at = _check_cron(schedule.cron_expr, schedule.timezone)

    await db.flush()
    # `updated_at` 由 SQL 端 `onupdate=func.now()` 计算，flush 后该列被标记为
    # 过期。若此时直接访问，SQLAlchemy 会尝试懒加载同一条记录——而那句
    # 额外的 SELECT 发生在 greenlet 上下文之外，asyncpg 直接抛 MissingGreenlet。
    # 显式 refresh 把这次读放回 await 里，同时也保证响应中的 updated_at
    # 是数据库真实写入的值，不是 flush 前的旧值。
    await db.refresh(schedule)
    logger.info("schedule_updated", schedule_id=schedule.id, fields=sorted(changes))
    return await _to_out(db, schedule, user)


async def set_enabled(
    db: AsyncSession, *, user: User, schedule_id: str, enabled: bool
) -> ScheduleOut:
    """启停（PATCH 的语义子集，独立入口便于审计区分）。"""
    return await update_schedule(
        db, user=user, schedule_id=schedule_id, payload=ScheduleUpdate(enabled=enabled)
    )


async def delete_schedule(db: AsyncSession, *, user: User, schedule_id: str) -> None:
    """删除规则。

    级联删除触发历史；已产生的需求与 Run 保留
    （`requests.source_schedule_id` 由外键 SET NULL 置空）。
    """
    schedule = await _require_owner_write(db, schedule_id, user)
    await db.delete(schedule)
    await db.flush()
    logger.info("schedule_deleted", schedule_id=schedule_id, owner_id=user.id)


async def get_schedule(
    db: AsyncSession, *, user: User, schedule_id: str
) -> ScheduleOut:
    schedule = await _require_owner_view(db, schedule_id, user)
    return await _to_out(db, schedule, user)


async def list_schedules(
    db: AsyncSession,
    *,
    user: User,
    page: int = 1,
    page_size: int = 20,
    enabled: bool | None = None,
) -> tuple[list[ScheduleOut], int]:
    """列表：member 仅自己，admin 全部。"""
    stmt = select(Schedule)
    count_stmt = select(func.count()).select_from(Schedule)
    if user.role != "admin":
        stmt = stmt.where(Schedule.owner_id == user.id)
        count_stmt = count_stmt.where(Schedule.owner_id == user.id)
    if enabled is not None:
        stmt = stmt.where(Schedule.enabled.is_(enabled))
        count_stmt = count_stmt.where(Schedule.enabled.is_(enabled))

    total = (await db.execute(count_stmt)).scalar_one()
    stmt = (
        stmt.order_by(Schedule.created_at.desc(), Schedule.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = (await db.execute(stmt)).scalars().all()
    return [await _to_out(db, s, user) for s in rows], total
