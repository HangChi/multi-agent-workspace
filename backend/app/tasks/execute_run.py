"""Celery 任务：execute_run（T069，US5）。

入队后由 Worker 消费（FR-013/FR-014）：
1. Redis 分布式锁（SET NX EX）防重复执行（同 run 被并发入队/重试时只跑一次）
2. 加载 Run + Agent（配置：model/temperature/max_steps/cost_limit）+ Request（需求文本）
3. 构造 ReActRunner 执行，逐步骤写 steps 表 + 累计 cost + 发布事件
4. 终态：completed（result）/ failed（可读错误）/ timeout / cancelled（Worker 发现已取消则跳过）
5. needs_input：挂起等待 API reply 恢复（FR-021）

任务为同步 def（Celery 默认进程池），内部 asyncio.run 驱动 async 执行链。
测试环境（TESTING=1）短路：不真正执行，直接标记 completed（由集成测试 mock）。
"""

import asyncio
import os

import redis
import structlog
from sqlalchemy import delete, select
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.db import SessionLocal
from app.executors.http import default_executors
from app.executors.llm import LiteLLMClient, LLMClientError
from app.models.agent import Agent
from app.models.request import Request
from app.models.run import Run
from app.models.step import Step
from app.orchestrator.runner import (
    CostLimitExceeded,
    ExecutionTimeout,
    MaxStepsExceeded,
    NeedsInput,
    ReActRunner,
)
from app.orchestrator.state_machine import RunStatus, transition
from app.services.quota_service import QuotaExceeded, assert_user_quota
from app.tasks import events as run_events
from app.tasks.celery_app import celery_app

logger = structlog.get_logger(__name__)

_LOCK_TTL_SECONDS = 3600  # 与 Agent.max_run_seconds 上限对齐（默认 1800，留余量）


def _acquire_lock(run_id: str) -> bool:
    """Redis SET NX EX：获取执行锁，失败说明已有 Worker 在执行。"""
    client = redis.from_url(settings.redis_url, decode_responses=True)
    try:
        return bool(client.set(f"run:lock:{run_id}", "1", nx=True, ex=_LOCK_TTL_SECONDS))
    finally:
        client.close()


def _release_lock(run_id: str) -> None:
    client = redis.from_url(settings.redis_url, decode_responses=True)
    try:
        client.delete(f"run:lock:{run_id}")
    finally:
        client.close()


async def _load_context(db, run_id: str) -> tuple[Run, Agent | None, Request]:
    """加载 Run（含 steps 惰性）+ Agent（按 assignee_id）+ Request。"""
    stmt = select(Run).options(selectinload(Run.steps)).where(Run.id == run_id)
    run = (await db.execute(stmt)).scalars().first()
    if run is None:
        raise RuntimeError(f"Run {run_id} 不存在")

    agent: Agent | None = None
    if run.assignee_type == "agent":
        agent = await db.get(Agent, run.assignee_id)

    request = await db.get(Request, run.request_id)
    if request is None:
        raise RuntimeError(f"Request {run.request_id} 不存在")

    return run, agent, request


def _build_runner(run: Run, agent: Agent | None) -> ReActRunner:
    """按 Agent 配置构造 runner；无 Agent（组父 Run）用默认配置。"""
    if agent is not None:
        return ReActRunner(
            llm=LiteLLMClient(model=agent.model),
            executors=default_executors(),
            max_steps=agent.max_steps,
            timeout_seconds=agent.max_run_seconds,
            cost_limit_cents=agent.cost_limit_cents,
            system_prompt=agent.system_prompt,
        )
    # 组父 Run：仅做编排占位（fan_out 时子 Run 各自执行），直接完成不调 LLM
    return ReActRunner(
        llm=LiteLLMClient(),
        executors=default_executors(),
    )


async def _persist_steps(db, run: Run, steps: list[dict]) -> None:
    """批量写 steps 表（seq 唯一约束 per run）。"""
    for step in steps:
        db.add(
            Step(
                run_id=run.id,
                seq=step["seq"],
                kind=step["kind"],
                input=step.get("input"),
                output=step.get("output"),
                tool_name=step.get("tool_name"),
                duration_ms=step.get("duration_ms", 0),
                cost_cents=step.get("cost_cents", 0),
                status=step.get("status", "completed"),
            )
        )
    await db.flush()


def _request_text(request: Request) -> str:
    """拼需求文本（标题 + 描述 + 期望产出）。"""
    parts = [request.title]
    if request.description:
        parts.append(request.description)
    if request.expected_output:
        parts.append(f"期望产出：{request.expected_output}")
    return "\n".join(parts)


def _run_id_for_log(run: Run) -> str:
    return run.id[:8]


async def _execute(db, run: Run, agent: Agent | None) -> None:
    """真正的执行链：状态机 → runner → 持久化。"""
    # 已取消/终态：不再执行（Worker 发现取消直接跳过）
    if run.status == "cancelled":
        logger.info("run_cancelled_before_exec", run_id=_run_id_for_log(run))
        return

    request = await db.get(Request, run.request_id)
    if request is None:
        run.error = "需求不存在，无法执行"
        transition(run, RunStatus.FAILED)
        await db.commit()
        run_events.run_failed(run.id, run.error)
        return

    # 组父 Run（fan_out）：仅占位完成（子 Run 各自执行）。
    # 状态机要求 queued → running → completed：先迁移 running 再 completed。
    if run.assignee_type == "group":
        transition(run, RunStatus.RUNNING)
        await db.commit()
        run_events.status_change(run.id, run.status)
        transition(run, RunStatus.COMPLETED)
        run.result = {"note": "组父 Run：由子 Run 完成执行"}
        await db.commit()
        run_events.run_completed(run.id, run.result)
        return

    runner = _build_runner(run, agent)
    steps: list[dict] = []

    # FR-021 恢复执行（needs_input → running）：旧步骤已写库，重跑会撞 (run_id, seq) 唯一约束。
    # 先清理旧步骤（缺陷 C 修复），再注入用户回复（缺陷 A 修复）。
    old_steps = (
        (await db.execute(select(Step.id).where(Step.run_id == run.id))).scalars().all()
    )
    if old_steps:
        # 显式批量删除旧步骤（不触发 lazy 关系加载，兼容测试环境无 selectinload）
        await db.execute(delete(Step).where(Step.run_id == run.id))
        await db.commit()
        logger.info("run_reply_cleanup_steps", run_id=_run_id_for_log(run), removed=len(old_steps))
    user_reply = run.user_reply
    extra_messages = [user_reply] if user_reply else None

    async def save_step(step: dict) -> None:
        steps.append(step)
        # 维护 Run.current_step（契约字段：已执行到第几步；状态机复位时置 0）
        # 每步立即落库，前端在运行中 refetch 即可看到进度增长。
        seq = step.get("seq")
        if isinstance(seq, int) and seq > run.current_step:
            run.current_step = seq
            await db.commit()

    async def publish_event(event: dict) -> None:
        # Worker 侧发布（events.py 内部处理事件循环）
        run_events._publish(run.id, event)

    async def set_status(status: str) -> None:
        await db.commit()  # 状态先落库（前端可查）

    async def set_cost(cost_cents: int) -> None:
        await db.commit()

    async def check_cancelled() -> bool:
        """FR-018 取消检查：每轮循环前查 DB 最新状态，外部 cancel 则终止执行。"""
        fresh = await db.get(Run, run.id)
        return fresh is not None and fresh.status == "cancelled"

    try:
        final_status = await runner.run(
            run=run,
            request_text=_request_text(request),
            save_step=save_step,
            publish_event=publish_event,
            set_status=set_status,
            set_cost=set_cost,
            extra_user_messages=extra_messages,
            should_abort=check_cancelled,
        )
        await _persist_steps(db, run, steps)
        await db.commit()
        # FR-018：runner 可能以 cancelled 返回（外部取消），不发 completed 事件
        if final_status == "cancelled":
            run_events.run_cancelled(run.id)
        else:
            run_events.run_completed(run.id, run.result)
    except NeedsInput as exc:
        await _persist_steps(db, run, steps)
        await db.commit()
        run_events.needs_input(run.id, str(exc))
    except (MaxStepsExceeded, CostLimitExceeded, ExecutionTimeout) as exc:
        await _persist_steps(db, run, steps)
        await db.commit()
        run_events.run_failed(run.id, run.error or str(exc))
    except LLMClientError as exc:
        run.error = str(exc)
        transition(run, RunStatus.FAILED)
        await db.commit()
        run_events.run_failed(run.id, run.error)
    except Exception as exc:  # 兜底：任何异常都转为可读失败（FR-017）
        logger.exception("execute_run_unexpected", run_id=_run_id_for_log(run))
        run.error = f"执行异常: {exc}"
        transition(run, RunStatus.FAILED)
        await db.commit()
        run_events.run_failed(run.id, run.error)


@celery_app.task(name="app.tasks.execute_run.execute_run", bind=True, max_retries=2)
def execute_run(self, run_id: str) -> str:
    """Worker 消费：执行一个 Run（同步 def，内部 async 驱动）。

    Returns:
        str: 最终状态（completed/failed/timeout/cancelled/needs_input/skipped）
    """
    if os.getenv("TESTING") == "1":
        # 测试环境短路：不连 broker/DB，直接返回（集成测试自行 mock 执行链）
        return "skipped-test"

    if not _acquire_lock(run_id):
        logger.warning("run_lock_busy", run_id=run_id)
        return "busy"

    async def _main() -> str:
        async with SessionLocal() as db:
            run, agent, _ = await _load_context(db, run_id)
            if run.status == "cancelled":
                return "cancelled"
            # T077 配额兜底：Worker 消费前再校验一次（分配后配额可能已变化）。
            # 已入队任务不因配额变化回滚（软约束），但防止越过配额执行。
            try:
                await assert_user_quota(db, run.owner_id)
            except QuotaExceeded:
                run.error = "月配额已用尽，任务被拒绝执行"
                transition(run, RunStatus.FAILED)
                await db.commit()
                run_events.run_failed(run_id, run.error)
                return "failed"
            await _execute(db, run, agent)
            await db.refresh(run)
            return run.status

    try:
        return asyncio.run(_main())
    except Exception as exc:  # 不可恢复错误：标记失败（避免任务死循环）
        logger.exception("execute_run_fatal", run_id=run_id, error=str(exc))
        error_text = str(exc)

        async def _fail() -> None:
            async with SessionLocal() as db:
                run = await db.get(Run, run_id)
                if run is not None:
                    run.error = f"执行器致命错误: {error_text}"
                    # 状态机要求 queued → running → failed；若仍在 queued（任务消费即崩），
                    # 先合法迁移到 running 再 failed，避免 TransitionError
                    if run.status == "queued":
                        transition(run, RunStatus.RUNNING)
                    transition(run, RunStatus.FAILED)
                    await db.commit()
                    run_events.run_failed(run_id, run.error)
        asyncio.run(_fail())
        return "failed"
    finally:
        _release_lock(run_id)
