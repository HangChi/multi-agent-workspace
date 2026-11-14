"""分配服务（T054）：拖拽分配的校验、策略展开与 Run 创建入队（US4）。

对齐 contracts/api.md 分配契约（FR-010~013）：
- 幂等校验：需求状态非 created/assigned → 409（FR-012 已在执行拒绝）
- 目标校验：不存在/无权限 → 404；已归档 → 400
- 按策略建 Run：direct 1 个 / fan_out 1 父 Run + N 个子 Run（含 parent_run_id）
- 事务提交后：Redis 事件发布 + Celery 入队（失败不阻塞业务，仅告警；
  API 只入队，重活交 Worker，对齐 RT<100ms 目标）
- 入队成功 → 需求 status=running（FR-013）
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
from app.orchestrator.strategies import (
    NotImplementedStrategyError,
    StrategyResolver,
    Target,
)
from app.services.quota_service import assert_user_quota
from app.services.request_service import get_request

logger = structlog.get_logger(__name__)

# 需求可分配状态（created 未分配 / assigned 已分配待入队）
_ASSIGNABLE_STATUSES = frozenset({"created", "assigned"})


async def _load_agent(db: AsyncSession, agent_id: str, user_id: str) -> Agent:
    """目标 Agent 校验：不存在/无权限 → 404；已归档 → 400。"""
    agent = await db.get(Agent, agent_id)
    if agent is None or agent.owner_id != user_id:
        raise AppError("NOT_FOUND", "Agent 不存在")
    if agent.status != "active":
        raise AppError("INVALID_INPUT", "归档 Agent 不可分配")
    return agent


async def _load_group(db: AsyncSession, group_id: str, user_id: str) -> AgentGroup:
    """目标组校验（预加载有序成员）：不存在/无权限 → 404；已归档 → 400。"""
    stmt = (
        select(AgentGroup)
        .options(selectinload(AgentGroup.members).selectinload(GroupMember.agent))
        .where(AgentGroup.id == group_id)
    )
    group = (await db.execute(stmt)).scalars().first()
    if group is None or group.owner_id != user_id:
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
) -> Run:
    """构建 Run 对象（status=queued，owner 继承自需求）。"""
    return Run(
        request_id=request.id,
        parent_run_id=parent_run_id,
        owner_id=request.owner_id,
        assignee_type=assignee_type,
        assignee_id=assignee_id,
        strategy=strategy,
        status="queued",
    )


def _enqueue(run_ids: list[str]) -> None:
    """Celery 入队：每个 Run 一个任务（Phase 7 execute_run 消费）。

    失败不抛出（API 只入队，broker 抖动不应阻塞受理），仅记录告警；
    测试环境（TESTING=1）跳过真实 broker（执行链路 Phase 7 覆盖）。
    """
    if os.getenv("TESTING") == "1":
        return
    from app.tasks.celery_app import celery_app

    for run_id in run_ids:
        try:
            celery_app.send_task(
                "app.tasks.execute_run.execute_run", args=[run_id]
            )
        except (ConnectionError, OSError, ValueError) as exc:  # broker 抖动降级，不阻塞受理
            logger.warning("celery_enqueue_failed", run_id=run_id, error=str(exc))


async def _publish_assigned(run_ids: list[str]) -> None:
    """Redis 事件发布：run:{id} 频道发 status_change=queued。

    测试环境（TESTING=1）跳过真实 Redis（WS 链路 Phase 7 覆盖）。
    """
    if os.getenv("TESTING") == "1":
        return
    for run_id in run_ids:
        try:
            await publish_run_event(
                run_id, {"type": "status_change", "status": "queued"}
            )
        except (ConnectionError, OSError, ValueError) as exc:  # Redis 抖动降级，不阻塞受理
            logger.warning("event_publish_failed", run_id=run_id, error=str(exc))


async def assign(
    db: AsyncSession,
    *,
    request_id: str,
    user_id: str,
    assignee_type: str,
    assignee_id: str,
    strategy: str | None,
) -> tuple[str, list[str]]:
    """执行一次分配，返回 (parent_run_id, [run_ids])。

    流程：需求校验 → 幂等校验 → 目标校验 → 策略展开 → 建 Run →
    事务提交 → 事件发布 + Celery 入队。
    """
    # 1. 需求归属 + 存在性（行级锁：状态检查与迁移必须原子，防并发重复分配）
    request = await get_request(db, request_id, user_id, for_update=True)

    # 1.5 配额熔断（T077）：月用量 >= 上限 → 429 RATE_LIMITED（宪法「每个用户有月配额」）
    # 前置校验：不建 Run、需求状态保持不变（可重试）
    await assert_user_quota(db, user_id)

    # 2. 幂等校验（FR-012）
    if request.status not in _ASSIGNABLE_STATUSES:
        raise AppError("CONFLICT", "需求已在执行，不可重复分配")

    # 3. 目标校验 + 默认策略
    group = None
    if assignee_type == "agent":
        await _load_agent(db, assignee_id, user_id)
        if strategy is None:
            strategy = "direct"
    elif assignee_type == "group":
        group = await _load_group(db, assignee_id, user_id)
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

    if strategy == "direct":
        target = targets[0]
        run = _build_run(
            request,
            assignee_type=target.assignee_type,
            assignee_id=target.assignee_id,
            strategy=strategy,
        )
        db.add(run)
        runs.append(run)
    else:  # fan_out：父 Run + N 个子 Run
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
        for target in targets:
            child = _build_run(
                request,
                assignee_type=target.assignee_type,
                assignee_id=target.assignee_id,
                strategy=strategy,
                parent_run_id=parent.id,
            )
            db.add(child)
            runs.append(child)

    await db.flush()

    # 6. 需求入队 → running（FR-013）
    request.status = "running"

    # 7. 事务提交
    await db.commit()
    for run in runs:
        await db.refresh(run)

    run_ids = [run.id for run in runs]

    # 8. 事件发布 + Celery 入队（失败不阻塞业务）
    await _publish_assigned(run_ids)
    _enqueue(run_ids)

    # 返回父 Run id（direct 即唯一 Run；fan_out 为父 Run）
    return parent_run_id or runs[0].id, run_ids
