"""分配服务（T054 + 007）：拖拽分配的校验、策略展开与 Run 创建入队（US4）。

对齐 contracts/api.md 分配契约（FR-010~013）：
- 幂等校验：需求状态非 created/assigned/accepted → 409（FR-012 已在执行拒绝；
  007 加入 accepted——员工接收后的需求与自己的需求同权执行）
- 目标校验：不存在/无权限 → 404；已归档 → 400
- 按策略建 Run：direct 1 个 / fan_out 1 父 Run + N 个子 Run（含 parent_run_id）
- 事务提交后：Redis 事件发布 + Celery 入队（失败不阻塞业务，仅告警；
  API 只入队，重活交 Worker，对齐 RT<100ms 目标）
- 入队成功 → 需求 status=running（FR-013）

007 变更（research R-03）：
- 月配额入参从**操作人**改为 **request.owner_id**。旧口径下二者恒等所以无感；
  分派后操作人 ≠ owner，若按操作人扣配额，员工的月配额会被别人派来的活吃爆，
  且与 Run.owner_id 的既有继承分裂成双口径。成本恒记需求创建者。
"""

import os

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.errors import AppError
from app.core.events import publish_run_event
from app.models.agent import Agent
from app.models.group import AgentGroup
from app.models.group_member import GroupMember
from app.models.request import Request
from app.models.run import Run
from app.orchestrator.request_state_machine import AGENT_ASSIGNABLE_STATUSES
from app.orchestrator.strategies import (
    NotImplementedStrategyError,
    StrategyResolver,
    Target,
)
from app.services import agent_service, dependency_service, project_member_service
from app.services.external_invocation_service import snapshot_external_grants
from app.services.quota_service import assert_user_quota
from app.services.request_service import get_request
from app.services.tool_service import enabled_tool_specs, reconcile_agent_grants

logger = structlog.get_logger(__name__)

# 需求可分配状态（created 未分配 / assigned 已分配待入队 / accepted 员工已接收）。
# 单一来源在 request_state_machine——分派类准入与执行准入不再各写一份。
_ASSIGNABLE_STATUSES = frozenset(s.value for s in AGENT_ASSIGNABLE_STATUSES)


async def _load_agent(
    db: AsyncSession,
    agent_id: str,
    user_id: str,
    allowed_owner_ids: set[str] | None = None,
) -> Agent:
    """目标 Agent 校验：不存在/无权限 → 404；已归档 → 400。

    allowed_owner_ids（010）：项目参与者集合——共享项目里的 agent 对
    参与者开放，否则成员拖执行时用不了 owner 建的 agent，协作链断在
    分配那一步（US3 场景 3）。非共享场景不传，行为逐字节不变。

    R1：归属谓词走 `agent_service.get_agent`（单一判定点），这里只额外加
    「归档不可分配」这条业务校验——它不属于归属，不该混进归属判定里。
    """
    agent = await agent_service.get_agent(
        db, agent_id, user_id, allow_owner_ids=allowed_owner_ids
    )
    if agent.status != "active":
        raise AppError("INVALID_INPUT", "归档 Agent 不可分配")
    return agent


async def _load_group(
    db: AsyncSession,
    group_id: str,
    user_id: str,
    allowed_owner_ids: set[str] | None = None,
) -> AgentGroup:
    """目标组校验（预加载有序成员）：不存在/无权限 → 404；已归档 → 400。

    allowed_owner_ids（010）：同 _load_agent——共享项目的组对参与者开放。
    """
    stmt = (
        select(AgentGroup)
        .options(selectinload(AgentGroup.members).selectinload(GroupMember.agent))
        .where(AgentGroup.id == group_id)
    )
    group = (await db.execute(stmt)).scalars().first()
    if group is None:
        raise AppError("NOT_FOUND", "组不存在")
    if group.owner_id != user_id and (
        allowed_owner_ids is None or group.owner_id not in allowed_owner_ids
    ):
        raise AppError("NOT_FOUND", "组不存在")
    if group.status != "active":
        raise AppError("INVALID_INPUT", "归档组不可分配")
    return group


def _build_run(
    request: Request,
    *,
    assignee_type: str,
    assignee_id: str,
    strategy: str,
    parent_run_id: str | None = None,
    sequence: int = 0,
) -> Run:
    """构建 Run 对象（status=queued，owner 继承自需求）。"""
    return Run(
        request_id=request.id,
        parent_run_id=parent_run_id,
        owner_id=request.owner_id,
        assignee_type=assignee_type,
        assignee_id=assignee_id,
        strategy=strategy,
        sequence=sequence,
        status="queued",
    )


def _enqueue_ids(strategy: str, runs: list[Run]) -> list[str]:
    """决定入队的 Run。

    pipeline 只入队第一阶段：后续阶段由前一段完成时链式触发，
    父 Run 不入队（由最后一阶段收敛），否则会像 fan_out 一样立刻"完成"。
    router 只建一个父 Run，由它自己在执行时选成员，正常入队。
    """
    if strategy == "pipeline":
        return [run.id for run in runs if run.sequence == 1]
    return [run.id for run in runs]


def _enqueue(run_ids: list[str]) -> None:
    """Celery 入队：每个 Run 一个任务（Phase 7 execute_run 消费）。

    失败不抛出（API 只入队，broker 抖动不应阻塞受理），仅记录告警；
    测试环境（TESTING=1）跳过真实 broker（执行链路 Phase 7 覆盖）。

    注意必须捕获宽泛异常：kombu 抛的是 ``kombu.exceptions.OperationalError``
    （KombuError 的子类），redis-py 抛的是 ``redis.exceptions.ConnectionError``
    （RedisError 的子类），两者都不是内建 ``ConnectionError``/``OSError``。
    只写内建类型会让"broker 抖动降级"完全失效：事务已提交、客户端却收到 500。
    """
    if os.getenv("TESTING") == "1":
        return
    from app.tasks.celery_app import celery_app

    for run_id in run_ids:
        try:
            celery_app.send_task("app.tasks.execute_run.execute_run", args=[run_id])
        except Exception as exc:  # noqa: BLE001 - broker 抖动降级，不阻塞受理
            logger.warning(
                "celery_enqueue_failed", run_id=run_id, error=type(exc).__name__
            )


async def _publish_assigned(run_ids: list[str]) -> None:
    """Redis 事件发布：run:{id} 频道发 status_change=queued。

    测试环境（TESTING=1）跳过真实 Redis（WS 链路 Phase 7 覆盖）。
    异常类型同 ``_enqueue``：redis-py 的 ConnectionError/TimeoutError 都不是内建类型。
    """
    if os.getenv("TESTING") == "1":
        return
    for run_id in run_ids:
        try:
            await publish_run_event(
                run_id, {"type": "status_change", "status": "queued"}
            )
        except Exception as exc:  # noqa: BLE001 - Redis 抖动降级，不阻塞受理
            logger.warning(
                "event_publish_failed", run_id=run_id, error=type(exc).__name__
            )


async def assign(
    db: AsyncSession,
    *,
    request_id: str,
    user_id: str,
    assignee_type: str,
    assignee_id: str,
    strategy: str | None,
) -> tuple[str, list[Run], str]:
    """执行一次分配，返回 (parent_run_id, runs, strategy)。

    只 flush、不提交：调用方在写完审计后自行 commit 一次，再调用
    `dispatch_assigned()` 发布事件并投递 Celery（docs/code-audit #9）。
    """
    # 1. 需求归属 + 存在性（行级锁：状态检查与迁移必须原子，防并发重复分配）
    #    007：谓词为 owner-or-assignee（request_service._check_owner），员工可
    #    以执行分派给自己且已接收的需求。010：再加一肢「项目成员」——成员
    #    拖共享项目里的需求执行是核心协作动作（allow_member 快路，
    #    _check_owner 本身不动）。
    # 019：分配是写操作——只读成员能看这条需求，但不能把它推去执行
    request = await get_request(
        db, request_id, user_id, for_update=True, allow_member=True, require_write=True
    )

    # 1.5 配额熔断（T077 + 007）：月用量 >= 上限 → 429 RATE_LIMITED（宪法「每个用户有月配额」）
    # 入参是 request.owner_id 而非操作人——分派后经手人 ≠ owner，成本恒记
    # 需求创建者（research R-03 / FR-009 / SC-005 双边断言）。
    # 前置校验：不建 Run、需求状态保持不变（可重试）
    await assert_user_quota(db, request.owner_id)

    # 2. 幂等校验（FR-012）
    if request.status not in _ASSIGNABLE_STATUSES:
        raise AppError("CONFLICT", "需求已在执行，不可重复分配")

    # 2.5 依赖门禁（009，FR-004）：有未完成前置 → 409 且消息逐条。
    # 位置在目标校验之前——「被挡住」与「目标错」是两个独立错误，先报更
    # 根本的那个；也省掉一次无谓的目标解析。查询是一次带索引的半连接
    # （ix_request_dependencies_request_id），SC-003 的 RT 预算靠它。
    # user 落点（拖员工转交）不查——责任流转与执行启动是两个关注点（FR-005）。
    if assignee_type in ("agent", "group"):
        await dependency_service.assert_not_blocked(db, request.id)

    # 2.7 010 共享：需求在共享项目里时，目标池 = 项目参与者（owner+成员）
    # 的 agent/组——成员用 owner 建的 agent 执行，协作链才不断。
    allowed_owner_ids: set[str] | None = None
    if request.project_id:
        allowed_owner_ids = set(
            await project_member_service.participant_user_ids(db, request.project_id)
        )

    # 3. 目标校验 + 默认策略
    group = None
    if assignee_type == "agent":
        await _load_agent(db, assignee_id, user_id, allowed_owner_ids)
        if strategy is None:
            strategy = "direct"
    elif assignee_type == "group":
        group = await _load_group(db, assignee_id, user_id, allowed_owner_ids)
        if strategy is None:
            strategy = group.routing_strategy
    else:
        raise AppError("INVALID_INPUT", "assignee_type 仅支持 agent|group")

    # 4. 策略展开（pipeline/router → 400「未实现」）
    try:
        targets: list[Target] = StrategyResolver.expand(
            strategy=strategy,
            assignee_type=assignee_type,
            assignee_id=assignee_id,
            group=group,
        )
    except NotImplementedStrategyError as exc:
        raise AppError("INVALID_INPUT", str(exc))
    except ValueError as exc:
        raise AppError("INVALID_INPUT", str(exc))

    # 5. 建 Run
    runs: list[Run] = []
    parent_run_id: str | None = None

    if strategy in ("direct", "router"):
        # direct：目标本身；router：父 Run 自执行（运行时选成员）
        target = targets[0]
        run = _build_run(
            request,
            assignee_type=target.assignee_type,
            assignee_id=target.assignee_id,
            strategy=strategy,
        )
        db.add(run)
        runs.append(run)
    else:  # fan_out（并发）/ pipeline（顺序）：父 Run + N 个子 Run
        parent = _build_run(
            request,
            assignee_type="group",
            assignee_id=assignee_id,
            strategy=strategy,
        )
        db.add(parent)
        await db.flush()  # 取父 Run id
        parent_run_id = parent.id
        runs.append(parent)
        for index, target in enumerate(targets, start=1):
            child = _build_run(
                request,
                assignee_type=target.assignee_type,
                assignee_id=target.assignee_id,
                strategy=strategy,
                parent_run_id=parent.id,
                # pipeline 记录阶段序号（1..N）；fan_out 无序
                sequence=index if strategy == "pipeline" else 0,
            )
            db.add(child)
            runs.append(child)

    # Freeze direct/child grants before enqueue, so edits made while queued can
    # revoke but never silently add capabilities to this run. Router parents do
    # not know their selected member yet; those snapshot at first execution.
    for run in runs:
        if run.assignee_type == "group" and run.strategy != "router":
            run.tool_grants_snapshot = []  # Orchestration-only parent: no tools.
    await db.flush()
    agent_runs = [run for run in runs if run.assignee_type == "agent"]
    if agent_runs:
        enabled = [spec.name for spec in await enabled_tool_specs(db, lock=True)]
        for run in agent_runs:
            agent = await db.get(Agent, run.assignee_id)
            if agent is None:
                raise AppError("NOT_FOUND", "Agent 不存在")
            run.tool_grants_snapshot = list(reconcile_agent_grants(agent, enabled))
            await snapshot_external_grants(
                db,
                run_id=run.id,
                owner_id=run.owner_id,
                agent_id=agent.id,
            )

    await db.flush()

    # 6. 需求入队 → running（FR-013）
    request.status = "running"

    # 7. 只 flush：提交由调用方（assign 端点 / 调度器）统一执行一次，
    #    审计与业务因此落在同一事务里（docs/code-audit #9）
    await db.flush()
    for run in runs:
        await db.refresh(run)

    return parent_run_id or runs[0].id, runs, strategy


async def dispatch_assigned(runs: list[Run], *, strategy: str) -> None:
    """提交之后发布 assigned 事件并入 Celery 队列（失败不阻塞业务）。

    必须在 commit 之后调用：Worker 拿到 run_id 就要能读到这一行，
    先入队再提交会让 Worker 偶发拿到「不存在的 Run」。
    """
    run_ids = [run.id for run in runs]
    await _publish_assigned(run_ids)
    _enqueue(_enqueue_ids(strategy, runs))
