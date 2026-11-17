"""调度规则服务（T017，US1）：CRUD + 六步校验 + next_run_at 重算。

三段各自的责任边界：

1. **校验顺序**是契约（contracts/api.md §1）。顺序决定用户先看到哪个错误，
   把目标校验提到 cron 之前会让用户改完目标才发现表达式也是错的。
2. **`next_run_at` 何时重算**是语义。改 cron/时区/启用态要重算，
   改模板/目标不重算——时间语义没变，重算只会让用户看到莫名的跳动。
3. **越权与不存在同响应**。用 403 区分"存在但无权"会让任何人可枚举
   他人的调度 id（403 = 存在，404 = 不存在）。
"""

from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.core.config import settings
from app.models.agent import Agent
from app.models.group import AgentGroup
from app.models.request import Request
from app.models.schedule import Schedule, ScheduleTrigger
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
from app.services import assignment_service
from app.services.quota_service import assert_user_quota
from app.services.schedule_cron import (
    CronExpressionError,
    count_between,
    next_after,
    parse_cron,
    validate_cron,
)

logger = structlog.get_logger(__name__)

#: 判定"前次未结束"时的非终态集合。与 request_service 的口径一致：
#: 只要还有一个 Run 没结束，需求就仍在执行中。
_UNFINISHED_REQUEST_STATUSES = frozenset({"created", "assigned", "running"})


def _missed_grace() -> timedelta:
    """宽限期：超过这么久没处理就算"错过"，不再补跑。

    90s = 扫描周期 60s + 一次处理的余量，避免一次短暂抖动就被记成错过。
    每次现读 settings 而不是模块级常量：测试要能按用例调小它来验证
    「刚好在宽限期内」与「刚好超过」的边界，模块级常量导入后就改不动了。
    """
    return timedelta(seconds=settings.schedule_missed_grace_seconds)


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


# =====================================================================
# 触发器处理（US2/US3）：判定顺序即契约
# =====================================================================
#
# 契约（contracts/api.md §3）规定的 7 步顺序，顺序本身不可调换：
#
#   1. 未启用                → 直接返回（不写记录、不推进）
#   2. next_run_at 为 None   → 直接返回（CHECK 约束保证不该发生）
#   3. lag > 宽限期          → missed：合并计数、跳过不补跑
#   4. 前次需求未结束         → skipped_busy
#   5. 目标/创建者不可用      → failed（计入连续失败，达阈值停用）
#   6. 配额不足              → failed（配额原因，不计入自动停用）
#   7. 其余                 → fired：建单 + 分配
#
# **第 3 步必须早于第 4 步**。这是全文件最要紧的一处：停机 2 小时后恢复，
# 若先判"前次未结束"，这条规则会被记成 skipped_busy，用户看到的是
# "被跳过了"而不是"错过了 24 次"——真正的运维事实被掩盖了。而实际上
# 前次没结束往往正是因为停机（进程被杀，Run 停在 running）。


async def _insert_placeholder(
    db: AsyncSession, schedule: Schedule, planned: datetime
) -> bool:
    """幂等占位：插入本次处理的触发记录占位行。

    用 `ON CONFLICT DO NOTHING` 而不是"先查再插 + 捕获异常"：
    - 先查再插在并发下必然漏（查与插之间有窗口）。
    - 捕获 IntegrityError 需要在长事务里制造保存点，且 SQLAlchemy 的
      session 在异常后状态需要显式回滚，容易连带回滚掉本次已完成的建单。

    **先占位再干活**是关键取舍：若先建单、再写记录时崩溃，重启后会认为
    "这次没处理过"而重复建单——需求已存在但记录不存在，唯一约束拦不住。
    先占位则崩溃只会留下"占位了但没建单"，表现为这次触发丢失。
    整体取舍是「宁可漏跑也不重复跑」。

    Returns:
        True 表示占位成功（本次由我处理）；False 表示该计划时刻已处理过。
    """
    result = await db.execute(
        text(
            """
            INSERT INTO schedule_triggers
                (id, schedule_id, planned_at, result, missed_count, created_at)
            VALUES
                (gen_random_uuid(), :sid, :planned, 'fired', 0, now())
            ON CONFLICT (schedule_id, planned_at) DO NOTHING
            RETURNING id
            """
        ),
        {"sid": schedule.id, "planned": planned},
    )
    return result.first() is not None


async def _finish_trigger(
    db: AsyncSession,
    schedule: Schedule,
    planned: datetime,
    *,
    result: str,
    reason: str | None = None,
    fired_at: datetime | None = None,
    missed_count: int = 0,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
    request_id: str | None = None,
    run_id: str | None = None,
    next_run: datetime | None = None,
) -> ScheduleTrigger:
    """把占位行改写为最终结果，并推进规则状态（同一事务）。"""
    trigger = (
        (
            await db.execute(
                select(ScheduleTrigger).where(
                    ScheduleTrigger.schedule_id == schedule.id,
                    ScheduleTrigger.planned_at == planned,
                )
            )
        )
        .scalars()
        .first()
    )
    assert trigger is not None, "占位行应已存在"

    trigger.result = result
    trigger.reason = reason
    trigger.fired_at = fired_at
    trigger.missed_count = missed_count
    trigger.window_start = window_start
    trigger.window_end = window_end
    trigger.request_id = request_id
    trigger.run_id = run_id

    schedule.last_run_at = fired_at or planned
    schedule.last_result = result
    if next_run is not None:
        schedule.next_run_at = next_run
    return trigger


async def _previous_request_unfinished(db: AsyncSession, schedule: Schedule) -> bool:
    """第 4 步：本规则最近一次成功建的需求是否仍在执行中。

    只认 `fired` 记录：失败/跳过/错过都不产生需求，不该挡住下一次。
    """
    last = (
        await db.execute(
            select(ScheduleTrigger.request_id)
            .where(
                ScheduleTrigger.schedule_id == schedule.id,
                ScheduleTrigger.result == "fired",
                ScheduleTrigger.request_id.is_not(None),
            )
            .order_by(ScheduleTrigger.planned_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if last is None:
        return False
    request = await db.get(Request, last)
    if request is None:
        return False
    return request.status in _UNFINISHED_REQUEST_STATUSES


async def _owner_available(db: AsyncSession, schedule: Schedule) -> str | None:
    """第 5 步：目标与创建者是否仍然可用。

    Returns:
        不可用原因（面向用户的中文短句），可用时为 None。
    """
    owner = await db.get(User, schedule.owner_id)
    if owner is None or owner.status != "active":
        return "创建者已停用"

    target_type = schedule.target_type
    target: Agent | AgentGroup | None
    if target_type == ScheduleTargetType.agent.value:
        target = await db.get(Agent, schedule.target_id)
    else:
        target = await db.get(AgentGroup, schedule.target_id)

    if target is None:
        return "目标已删除"
    if target.owner_id != schedule.owner_id:
        # 目标被转交或原先就不是本人的：不允许借调度绕过归属校验
        return "目标不可用"
    if target.status != "active":
        return "目标已归档"
    return None


async def fire_schedule(
    db: AsyncSession,
    schedule: Schedule,
    *,
    now: datetime | None = None,
) -> ScheduleTrigger | None:
    """处理单个规则的一次到期（契约 §3 的 7 步判定）。

    Returns:
        处理后的触发记录；若本次无事可做（未启用、无下次时间、
        或该计划时刻已处理过）返回 None。
    """
    now = now or datetime.now(UTC)

    # 第 1 步
    if not schedule.enabled:
        return None
    # 第 2 步
    planned = schedule.next_run_at
    if planned is None:
        return None

    # 幂等占位（先占位再干活）
    if not await _insert_placeholder(db, schedule, planned):
        logger.info(
            "schedule_trigger_duplicate",
            schedule_id=schedule.id,
            planned_at=planned.isoformat(),
        )
        return None

    cron = parse_cron(schedule.cron_expr, schedule.timezone)
    lag = now - planned

    # 第 3 步：错过（必须先于"前次未结束"，否则停机恢复会被误记为 busy）
    grace = _missed_grace()
    if lag > grace:
        window_end = now
        # 统计 [planned, now] 闭区间内的全部刻度。**不能传 now - grace**：
        # 那个范围会漏掉「恰好落在 now 上的那一班」，得 24 而非契约实测的 25。
        # 语义上也应当算上它：错过窗口一旦判定成立，当前这一班也和前面的一样
        # 已经过期（我们不会在同一轮里既记错过又补跑它），next_run_at 直接推进
        # 到 next_after(now)，即「跳过到 now 之后」。
        missed = count_between(cron, planned, now)
        missed = max(missed, 1)
        next_run = next_after(cron, now)
        trigger = await _finish_trigger(
            db,
            schedule,
            planned,
            result="missed",
            reason=f"服务未运行，错过 {missed} 次",
            missed_count=missed,
            window_start=planned,
            window_end=window_end,
            next_run=next_run,
        )
        await db.commit()
        logger.warning(
            "schedule_missed",
            schedule_id=schedule.id,
            missed=missed,
            window_start=planned.isoformat(),
        )
        return trigger

    # 第 4 步：前次未结束
    if await _previous_request_unfinished(db, schedule):
        trigger = await _finish_trigger(
            db,
            schedule,
            planned,
            result="skipped_busy",
            reason="上次运行尚未结束",
            fired_at=now,
            next_run=next_after(cron, planned),
        )
        await db.commit()
        logger.info("schedule_skipped_busy", schedule_id=schedule.id)
        return trigger

    # 第 5 步：目标/创建者可用性
    unavailable = await _owner_available(db, schedule)
    if unavailable is not None:
        return await _record_failure(db, schedule, planned, reason=unavailable, now=now)

    # 第 6 步：配额（交给 assign 内部判定，这里提前判是为了记成"配额原因"
    # 而不是笼统的失败；且配额失败不该计入自动停用阈值）
    try:
        await assert_user_quota(db, schedule.owner_id)
    except AppError:
        trigger = await _finish_trigger(
            db,
            schedule,
            planned,
            result="failed",
            reason="本月配额已用尽",
            fired_at=now,
            next_run=next_after(cron, planned),
        )
        # 配额不足不累加 consecutive_failures：它不是规则本身的问题，
        # 累加会让规则在用户充值后仍被自动停用。
        await db.commit()
        logger.warning("schedule_quota_exhausted", schedule_id=schedule.id)
        return trigger

    # 第 7 步：建单 + 分配
    return await _fire_and_assign(db, schedule, planned, now=now)


async def _fire_and_assign(
    db: AsyncSession,
    schedule: Schedule,
    planned: datetime,
    *,
    now: datetime,
) -> ScheduleTrigger:
    """建单并交给既有分配逻辑。

    `assign()` 内部会 `db.commit()`（R-05 已实测），因此需求与 Run 的
    创建天然是一个原子事务；但它也会提交我们刚写的占位行——这正是我们
    想要的：占位与建单一起落地，崩溃后不会重复。

    `db.flush()` 必须早于 `assign()`：后者用 `get_request(..., for_update=True)`
    读需求，行必须已经存在于数据库（flush 而非 commit，仍在同一事务内）。
    """
    request = Request(
        owner_id=schedule.owner_id,
        title=schedule.template_title,
        description=schedule.template_description,
        expected_output=schedule.template_expected_output,
        priority=schedule.template_priority,
        status="created",
        attachments=[],
        source_schedule_id=schedule.id,
    )
    db.add(request)
    await db.flush()

    try:
        parent_run_id, run_ids = await assignment_service.assign(
            db,
            request_id=request.id,
            user_id=schedule.owner_id,
            assignee_type=schedule.target_type,
            assignee_id=schedule.target_id,
            strategy=schedule.target_strategy,
        )
    except AppError as exc:
        # assign 失败 → 整个事务（含占位行）回滚 → 换新事务写失败记录。
        # 不换新事务的话，那条 failed 记录会跟着回滚一起消失，
        # 用户只看到"什么都没发生"，连"失败过"都不知道。
        await db.rollback()
        logger.warning(
            "schedule_assign_failed",
            schedule_id=schedule.id,
            code=exc.code,
            message=exc.message,
        )
        return await _record_failure(
            db,
            schedule,
            planned,
            reason=_translate_assign_failure(exc),
            now=now,
            fresh=True,
        )

    trigger = await _finish_trigger(
        db,
        schedule,
        planned,
        result="fired",
        fired_at=datetime.now(UTC),
        request_id=request.id,
        run_id=parent_run_id,
        next_run=next_after(parse_cron(schedule.cron_expr, schedule.timezone), planned),
    )
    # 成功即清空连续失败计数：规则能跑通说明之前的问题已消失
    schedule.consecutive_failures = 0
    schedule.auto_disabled_reason = None
    await db.commit()
    logger.info(
        "schedule_fired",
        schedule_id=schedule.id,
        request_id=request.id,
        run_id=parent_run_id,
        runs=len(run_ids),
    )
    return trigger


def _translate_assign_failure(exc: AppError) -> str:
    """把分配失败翻成契约 §2 的措辞白名单。

    契约限定了 `reason` 的取值集合，不得出现 UUID、表名、异常类名。
    """
    message = exc.message
    if "归档" in message:
        return "目标已归档"
    if "不存在" in message:
        return "目标已删除"
    if "配额" in message or exc.code == "RATE_LIMITED":
        return "本月配额已用尽"
    return "目标不可用"


async def _record_failure(
    db: AsyncSession,
    schedule: Schedule,
    planned: datetime,
    *,
    reason: str,
    now: datetime,
    fresh: bool = False,
) -> ScheduleTrigger:
    """记录一次失败并推进时间，必要时自动停用。

    Args:
        fresh: 调用方已回滚事务，需要重新占位（占位行已随回滚消失）。
    """
    if fresh:
        await _insert_placeholder(db, schedule, planned)
        # 回滚后 schedule 对象已过期，重新读一次拿到数据库里的真实状态
        await db.refresh(schedule)

    cron = parse_cron(schedule.cron_expr, schedule.timezone)
    next_run = next_after(cron, planned)

    trigger = await _finish_trigger(
        db,
        schedule,
        planned,
        result="failed",
        reason=reason,
        fired_at=now,
        next_run=next_run,
    )
    schedule.consecutive_failures += 1

    if schedule.consecutive_failures >= settings.schedule_max_failures:
        # 自动停用：留着 next_run_at 会让列表显示"下次：明天 9 点"，
        # 而它已经不会再跑了。停用态下 next_run_at 必须为 NULL
        # （CHECK 约束也这么要求）。
        schedule.enabled = False
        schedule.next_run_at = None
        schedule.auto_disabled_reason = (
            f"连续 {schedule.consecutive_failures} 次失败已自动停用：{reason}"
        )
        logger.warning(
            "schedule_auto_disabled",
            schedule_id=schedule.id,
            failures=schedule.consecutive_failures,
        )

    await db.commit()
    return trigger


async def run_due_schedules(db: AsyncSession, *, now: datetime | None = None) -> int:
    """扫描并处理所有到期规则，返回处理条数（Celery Beat 每分钟调用）。

    每条规则独立加锁、独立事务：一条出错不能带走整批（FR-018）。
    """
    if not settings.schedule_scan_enabled:
        logger.info("schedule_scan_disabled")
        return 0

    now = now or datetime.now(UTC)
    due = (
        (
            await db.execute(
                select(Schedule.id)
                .where(Schedule.enabled.is_(True), Schedule.next_run_at <= now)
                .order_by(Schedule.next_run_at)
                .limit(settings.schedule_batch_size)
            )
        )
        .scalars()
        .all()
    )

    processed = 0
    for schedule_id in due:
        try:
            # 行级锁：扫描与用户 PATCH 并发时，读到的是同一完整版本
            schedule = (
                (
                    await db.execute(
                        select(Schedule)
                        .where(Schedule.id == schedule_id)
                        .with_for_update()
                    )
                )
                .scalars()
                .first()
            )
            if schedule is None:
                continue
            if await fire_schedule(db, schedule, now=now) is not None:
                processed += 1
        except Exception:
            # 单条失败不拖垮整批：回滚该条、记日志、继续下一条
            await db.rollback()
            logger.exception("schedule_fire_error", schedule_id=schedule_id)
    return processed
