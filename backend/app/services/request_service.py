"""需求服务（T044）：CRUD + 列表查询（状态/关键词/优先级/分页）+ 归属校验。

对齐 contracts/api.md 需求部分：
- GET /requests: ?status=&q=&priority=&page=&page_size=
- POST /requests: 创建（status=created）
- GET /requests/{id}: 详情（含 attachments）
"""

import os

import structlog
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.api.messages import raise_for_missing
from app.models.project import Project
from app.models.request import Request
from app.models.request_dispatch import RequestDispatch
from app.models.run import Run
from app.models.user import User
from app.orchestrator.request_state_machine import RequestStatus
from app.orchestrator.state_machine import (
    ACTIVE_RUN_STATUSES,
    FAILED_RUN_STATUSES,
    SUCCESS_RUN_STATUSES,
)
from app.schemas.request import RequestCreate, RequestUpdate, RunOutcomeSummary
from app.services import dependency_service, project_member_service, project_service

logger = structlog.get_logger(__name__)

# Run 的非终态：只要还有一个没结束，需求就仍在执行中。
#
# R3：改为引用状态机导出的集合。此前这里是 `{queued, running, needs_input}`
# 的字面量，006 引入 `retrying` 后没同步——等待重试的 Run 既不算「活跃」，
# 于是 `settle` 会把它当成「所有 Run 都已结束」，在重试真正跑完之前就把需求
# 收敛成 completed/failed。看板会在重试期间闪现错误终态，重试成功后再被
# 后续 settle 纠回来（若重试路径没再调 settle 就永久错了）。
_ACTIVE_RUN_STATUSES = ACTIVE_RUN_STATUSES


async def assignee_emails(db: AsyncSession, user_ids: list[str]) -> dict[str, str]:
    """批量取用户邮箱（需求列表/详情展示「当前接收人」用，一次 IN 查询）。"""
    ids = [uid for uid in user_ids if uid]
    if not ids:
        return {}
    rows = (await db.execute(select(User.id, User.email).where(User.id.in_(ids)))).all()
    return {row.id: row.email for row in rows}


async def dispatch_history(db: AsyncSession, request_id: str) -> list[RequestDispatch]:
    """经手链（详情页时间线）：按 seq 正序——「谁先谁后」是叙事顺序。

    排序键是 seq 不是 created_at：PG 的 now() 是事务开始时间，行锁等待会
    让后插入的事务持有更早的时间戳（data-model §3 / 模型注释）。
    """
    stmt = (
        select(RequestDispatch)
        .where(RequestDispatch.request_id == request_id)
        .order_by(RequestDispatch.seq)
    )
    return list((await db.execute(stmt)).scalars().all())


async def settle_request_status(db: AsyncSession, request_id: str) -> str | None:
    """把需求状态推进到与它名下所有 Run 的实际结果一致。

    分配时需求被置为 running，但此前没有任何代码把它推进到终态：看板的
    「已完成 / 失败」两列永远是空的，卡片一直停在「执行中」。规则保持可预期：
    还有未终态的 Run 就维持 running；否则 失败 > 取消 > 完成（任一子任务失败即
    整体失败，用户取消过就显示已取消）。

    只处理 running 的需求，不覆盖 reaper 的判定，也不把 created/assigned 提前收口。

    007：收敛到终态时给**创建者**发一条 `run_completed` 通知（FR-010）——
    经手人（接收人）可能不是创建者，执行结果要回到需求方眼前。

    009：收敛到终态后反查「谁在等这条需求」（依赖边），对**其余前置均已
    终态**的后续方发 `dependency_ready`（FR-007）。落点选这里而不是
    execute_run 各完成分支：settle 是终态唯一汇聚点，且 running 门禁
    天然保证幂等（R-02）。
    """
    request = await db.get(Request, request_id)
    # 只收口**执行中**的需求：created/pending/accepted 不该被 settle 提前推进
    # （它们要么还没有 Run，要么还在分派链路里），assigned 是遗留中间态、
    # 保留旧口径不动。这里刻意不用 `not in TERMINAL_STATUSES`——那会把
    # created/assigned 也纳进来，是行为变更而非重构。
    if request is None or request.status != RequestStatus.RUNNING.value:
        return None
    statuses = list(
        (await db.execute(select(Run.status).where(Run.request_id == request_id)))
        .scalars()
        .all()
    )
    if not statuses or any(status in _ACTIVE_RUN_STATUSES for status in statuses):
        return None
    if any(status in FAILED_RUN_STATUSES for status in statuses):
        request.status = "failed"
    elif any(status == "cancelled" for status in statuses):
        request.status = "cancelled"
    else:
        request.status = "completed"
    logger.info(
        "request_settled",
        request_id=request_id,
        status=request.status,
        runs=len(statuses),
    )
    # 007 FR-010：执行结果通知创建者（标题存需求标题快照，同分派通知纪律）
    from app.services.notification_service import create_notification

    await create_notification(
        db,
        user_id=request.owner_id,
        type_="run_completed",
        title=request.title,
        request_id=request.id,
        payload={"status": request.status},
    )
    # 009 FR-007/008：解锁通知（同事务；幂等由上面的 running 门禁保证）
    await _notify_ready_dependents(db, request)
    return request.status


async def _notify_ready_dependents(db: AsyncSession, request: Request) -> None:
    """给「等这条需求且其余前置均已终态」的后续方发 dependency_ready。

    受众 = 后续方 owner + assignee（非同一人时两条，同人去重）——「可以
    启动了」对经手人同样重要（他才是拖给 Agent 的人）。后续方已终态或
    在 running 时跳过（FR-007 场景 4/5；running 本不该有未完成前置，
    门禁保证，这里是防御）。
    """
    from app.services.notification_service import create_notification

    dependents = await dependency_service.ready_dependents(db, request.id)
    for dependent in dependents:
        recipients = {dependent.owner_id}
        if dependent.assignee_id:
            recipients.add(dependent.assignee_id)
        for user_id in recipients:
            await create_notification(
                db,
                user_id=user_id,
                type_="dependency_ready",
                title=dependent.title,
                request_id=dependent.id,
                payload={
                    "dependency_id": request.id,
                    "dependency_title": request.title,
                    # 前端按实际终态渲染三种措辞（completed/failed/
                    # cancelled），不拆三种通知类型
                    "dependency_status": request.status,
                },
            )
    # 021：前置齐了 → 对「预置了自动启动」的后续方投一条 Celery 消息。
    # 只入队、不在这里建 Run：settle 是终态汇聚点，不该被分配链路拖长；
    # 幂等与门禁由任务自己再校验一遍。
    if os.getenv("TESTING") != "1":
        for dependent in dependents:
            if dependent.auto_start and dependent.auto_agent_id:
                try:
                    from app.tasks.auto_start_request import auto_start_request

                    auto_start_request.delay(dependent.id)
                    logger.info(
                        "dependency_auto_start_enqueued", request_id=dependent.id
                    )
                except Exception as exc:  # noqa: BLE001 - 入队失败不该影响 settle
                    logger.warning(
                        "dependency_auto_start_enqueue_failed",
                        request_id=dependent.id,
                        error=f"{type(exc).__name__}: {exc}"[:200],
                    )

    if dependents:
        logger.info(
            "dependency_ready_notified",
            dependency_id=request.id,
            dependents=[d.id for d in dependents],
        )


async def run_outcome_summaries(
    db: AsyncSession, request_ids: list[str]
) -> dict[str, RunOutcomeSummary]:
    """按需求汇总其 Run 的结果构成（一次分组查询，供列表与详情复用）。

    只返回确实有 Run 的需求：未分配的需求不该出现「0/0」这种噪音。
    """
    if not request_ids:
        return {}
    rows = (
        await db.execute(
            select(Run.request_id, Run.status, func.count())
            .where(Run.request_id.in_(request_ids))
            .group_by(Run.request_id, Run.status)
        )
    ).all()
    counts: dict[str, RunOutcomeSummary] = {}
    for request_id, status, count in rows:
        summary = counts.setdefault(request_id, RunOutcomeSummary())
        summary.total += count
        if status in SUCCESS_RUN_STATUSES:
            summary.completed += count
        elif status in FAILED_RUN_STATUSES:
            summary.failed += count
        elif status == "cancelled":
            summary.cancelled += count
        elif status in _ACTIVE_RUN_STATUSES:
            summary.active += count
    return counts


def is_participant(request: Request, user_id: str) -> bool:
    """「创建者 ∪ 当前接收人」——需求的基本访问权谓词（R1）。

    这是需求访问权的**单一判定点**。此前同一谓词被复制了四处：
    `request_service._check_owner`、`dispatch_service._check_access`、
    `dependency_service._load_editable_request`、`api/v1/runs._get_owned_run`
    （后者还多一层「先按 owner 短路、再查 request 拿 assignee」的绕路）。
    四处一旦有一处加了新分支（例如 010 的项目成员），其余三处就静默落后。

    注意这里**不含**项目成员（010 的 `allow_member`）——成员资格要走
    `project_member_service.is_participant` 查库，放进纯谓词会让调用方
    以为它是免费的。需要成员的场景用 `get_request(..., allow_member=True)`。

    也**不含** admin：016 spec 明确「admin 读他人资源是否放行由各端点
    既有策略决定，本特性不改」。把 admin 塞进通用谓词等于替产品做了决策，
    所以只在确实需要 admin 的两处（WS 订阅）显式叠加，见 `ws` 的调用点。
    """
    return request.owner_id == user_id or request.assignee_id == user_id


def _check_owner(request: Request, user_id: str) -> None:
    """资源访问权（007）：创建者**或当前接收人**；010 起项目成员另行放行。

    旧口径仅 owner——分派后接收人会被拒，员工对「派给自己的需求」
    毫无操作权（research R-02）。

    错误码（016 起统一）：**非相关人 404**，message 与「真的不存在」逐字相同。
    此前这里回 403，与 007 之后的新端点（分派、依赖、项目成员）不一致，
    让「403 存在 / 404 不存在」的差异可被用来枚举他人需求 id
    （spec 016 research R-01）。注意 403 仍用于「参与者缺某个字段权限」，
    例如 assignee 改 project_id —— 他知道需求存在，只是没这个权限。
    """
    if not is_participant(request, user_id):
        # 文案与「真的不存在」逐字相同——这是 016 契约 §2 的硬要求
        raise_for_missing("request")


async def create_request(
    db: AsyncSession, *, user_id: str, payload: RequestCreate
) -> Request:
    """创建需求（member/admin 已由路由层校验）。

    008：`project_id` 非空时校验「存在 + 属本人 + active」，并双写
    `project_name` 快照（快照的两个写入点之一，另一个是项目改名）。
    """
    project_name: str | None = None
    if payload.project_id:
        project = await project_service.load_active_owned_project(
            db, payload.project_id, user_id
        )
        project_name = project.name
    request = Request(
        owner_id=user_id,
        title=payload.title,
        description=payload.description,
        expected_output=payload.expected_output,
        priority=payload.priority.value,
        project_id=payload.project_id,
        project_name=project_name,
    )
    db.add(request)
    await db.flush()
    return request


async def get_request(
    db: AsyncSession,
    request_id: str,
    user_id: str,
    *,
    for_update: bool = False,
    allow_member: bool = False,
    require_write: bool = False,
) -> Request:
    """取单个需求（归属校验）。

    Args:
        for_update: 加行级锁（SELECT ... FOR UPDATE）。用于「读状态 → 改状态」
            必须原子的场景：分配时若不加锁，两个并发请求会同时读到 created，
            各自建一组 Run（同一需求被执行两次、成本翻倍）。
        allow_member: 010 共享——**项目成员**也放行（owner ∪ assignee ∪
            成员）。默认 False：既有调用方行为逐字节不变（零回归的关键）。
            成员快路在 `_check_owner` **之前**判——那个函数对非相关人员
            抛 403，不先让成员过就会误伤。
        require_write: 019——写操作（改需求/分配）额外要求**项目内写权限**。
            只读成员（role=viewer）能读这条需求，但不能改；这里返回 403
            而不是 404：他知道项目与需求存在，只是不被允许动（016 的分工）。
            owner 与 assignee 不受此限（他们不是「靠成员身份」进来的）。
    """
    if for_update:
        stmt = select(Request).where(Request.id == request_id).with_for_update()
        request = (await db.execute(stmt)).scalars().first()
    else:
        request = await db.get(Request, request_id)
    if request is None:
        raise_for_missing("request")
    if (
        allow_member
        and request.project_id
        and await project_member_service.is_participant(db, request.project_id, user_id)
    ):
        # 019：只读成员能进到这里（可见性），但写操作要再过一道
        if require_write and not await project_member_service.can_write_in_project(
            db, request.project_id, user_id
        ):
            raise AppError("FORBIDDEN", "你在该项目是只读成员，不能修改需求或分配执行")
        return request
    _check_owner(request, user_id)
    return request


async def list_requests(
    db: AsyncSession,
    *,
    user_id: str,
    scope: str = "relevant",
    project_id: str | None = None,
    project: str | None = None,
    status: str | None = None,
    q: str | None = None,
    priority: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[Request], int]:
    """列表：scope=relevant（默认，我创建的 + 分派给我的）｜mine（仅我创建的）。

    relevant 是 007 的默认值——看板要能同时看到「我的活」和「派给我的活」。
    这是**只扩大不缩小**的契约变更（spec Assumptions 2）：不传 scope 的旧
    调用方从「只看自己的」变为「自己的 + 派给我的」，没有人因此看不到原来
    能看的东西。mine 保持 006 及以前逐字段一致（回归测试钉住）。

    008：`project_id`（具体项目，含归档——回看不拦）/ `project=none`
    （未归属）两个维度参数与 scope/status/q/priority 正交叠加。
    """
    if scope == "mine":
        conditions = [Request.owner_id == user_id]
    else:  # relevant：并集
        # 010：再加一肢「其项目的参与者（owner ∪ 成员）」——共享项目里的
        # 全部需求对所有参与者可见。参与项目 id 一次取出（owned ∪ 成员行），
        # 不逐条 EXISTS。
        participating = await project_member_service.participating_project_ids(
            db, user_id
        )
        conditions = [
            or_(
                Request.owner_id == user_id,
                Request.assignee_id == user_id,
                Request.project_id.in_(participating),
            )
        ]
    if project_id:
        # 过滤目标必须先校验：不存在/他人 → 404，不是静默空列表
        # （空列表会让人以为项目被清空了——SC 之外的可用性纪律）
        await project_service.project_exists_for_filter(db, project_id, user_id)
        conditions.append(Request.project_id == project_id)
    elif project == "none":
        conditions.append(Request.project_id.is_(None))
    if status:
        conditions.append(Request.status == status)
    if priority:
        conditions.append(Request.priority == priority)
    if q:
        like = f"%{q}%"
        conditions.append(
            or_(
                Request.title.ilike(like),
                Request.description.ilike(like),
                Request.expected_output.ilike(like),
            )
        )

    # 总数
    total = (
        await db.execute(select(func.count()).select_from(Request).where(*conditions))
    ).scalar_one()

    # 分页列表（默认按创建时间倒序）。id 作 tie-breaker：同批创建的需求
    # created_at 可能相同，只按时间排序翻页会重复或漏项。
    stmt = (
        select(Request)
        .where(*conditions)
        .order_by(Request.created_at.desc(), Request.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = list((await db.execute(stmt)).scalars().all())
    return rows, total


async def _can_change_project(db: AsyncSession, request: Request, user_id: str) -> bool:
    """010/R-03：谁可以改需求的 project_id——创建者 **或该项目 owner**。

    008 立此规则防「经手人把需求挪去自己项目，创建者视图静默丢活」。
    共享场景下同一个担忧放大到整个成员集合；收敛解：两个对项目结构
    负有责任的角色（建它的人、管这个项目的人）可以改，其余人不行。
    """
    if request.owner_id == user_id:
        return True
    if request.project_id:
        project = await db.get(Project, request.project_id)
        if project is not None and project.owner_id == user_id:
            return True
    return False


async def update_request(
    db: AsyncSession, *, request_id: str, user_id: str, payload: RequestUpdate
) -> Request:
    """编辑需求（归属校验）。

    008 字段级权限分流（R-05）：请求体**含** `project_id` 键时要求创建者
    （403），其余字段维持 007 的 owner-or-assignee 谓词。判定用「键是否存在」
    而不是「值是否变化」——显式传同值也要 403，客户端才能预判行为。
    """
    request = await get_request(
        db, request_id, user_id, allow_member=True, require_write=True
    )
    data = payload.model_dump(exclude_unset=True)

    if "project_id" in data and not await _can_change_project(db, request, user_id):
        raise AppError("FORBIDDEN", "仅创建者或项目创建者可变更需求的项目归属")
    # 021：自动启动会真的花钱（配额按需求创建者算），权限与项目归属同档
    if ("auto_start" in data or "auto_agent_id" in data) and not (
        await _can_change_project(db, request, user_id)
    ):
        raise AppError("FORBIDDEN", "仅创建者或项目创建者可设置自动启动")
    if "auto_start" in data:
        request.auto_start = bool(data["auto_start"])
    if "auto_agent_id" in data:
        agent_id = data["auto_agent_id"]
        if agent_id:
            # R1：归属谓词走 agent_service.get_agent。这里要求的是
            # **需求创建者自己的** Agent（自动启动用创建者的配额花钱），
            # 所以拿 request.owner_id 去判，而不是当前操作人。
            from app.services import agent_service

            await agent_service.get_agent(db, str(agent_id), request.owner_id)
            request.auto_agent_id = str(agent_id)
        else:
            request.auto_agent_id = None
    if "project_id" in data:
        if data["project_id"]:
            project = await project_service.load_active_owned_project(
                db, data["project_id"], user_id
            )
            request.project_id = project.id
            request.project_name = project.name
        else:
            # 显式 null = 解除归属（两列一起清：留 name 是孤儿名字）
            request.project_id = None
            request.project_name = None

    for field, value in data.items():
        if field == "project_id":
            continue  # 上面已单独处理（含权限与快照）
        if value is None and field in ("description", "expected_output"):
            setattr(request, field, None)
        elif value is not None:
            if field == "priority" and hasattr(value, "value"):
                value = value.value
            setattr(request, field, value)
    await db.flush()
    return request
