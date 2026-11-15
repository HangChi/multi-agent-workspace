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
from dataclasses import asdict
from typing import Any

import redis
import structlog
from sqlalchemy import delete, select
from sqlalchemy.orm import selectinload

from app.core.audit import write_audit
from app.core.config import settings
from app.core.db import SessionLocal
from app.executors.llm import LiteLLMClient, LLMClientError
from app.executors.sandbox import build_executors
from app.models.agent import Agent
from app.models.request import Request
from app.models.run import Run
from app.models.step import Step
from app.orchestrator.router import route
from app.orchestrator.runner import (
    CostLimitExceeded,
    ExecutionTimeout,
    MaxStepsExceeded,
    NeedsInput,
    ReActRunner,
)
from app.orchestrator.state_machine import RunStatus, transition
from app.services import egress_service, sandbox_service
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


def _build_runner(
    run: Run,
    agent: Agent | None,
    *,
    abort_check=None,
    on_sandbox_result=None,
) -> ReActRunner:
    """按 Agent 配置构造 runner；无 Agent（组父 Run）用默认配置。"""
    if agent is not None:
        return ReActRunner(
            llm=LiteLLMClient(model=agent.model),
            executors=build_executors(list(agent.tools or []), abort_check=abort_check),
            max_steps=agent.max_steps,
            timeout_seconds=agent.max_run_seconds,
            cost_limit_cents=agent.cost_limit_cents,
            system_prompt=agent.system_prompt,
            on_sandbox_result=on_sandbox_result,
        )
    # 组父 Run：仅做编排占位（fan_out 时子 Run 各自执行），直接完成不调 LLM
    return ReActRunner(
        llm=LiteLLMClient(),
        executors=build_executors(),
    )


async def _persist_steps(db, run: Run, steps: list[dict]) -> None:
    """批量写 steps 表（seq 唯一约束 per run）。"""
    for step in steps:
        if step.get("id") and await db.get(Step, step["id"]):
            continue
        db.add(
            Step(
                id=step.get("id"),
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


def _enqueue(run_ids: list[str]) -> None:
    """把 Run 入队（pipeline 链式推进用）。失败只告警，不影响当前执行结果。"""
    for run_id in run_ids:
        try:
            celery_app.send_task("app.tasks.execute_run.execute_run", args=[run_id])
        except (ConnectionError, OSError, ValueError) as exc:
            logger.warning("celery_enqueue_failed", run_id=run_id, error=str(exc))


async def _previous_stage_output(db, run: Run) -> str | None:
    """pipeline：取上一阶段的产出文本（result.answer）。"""
    prev = (
        await db.execute(
            select(Run).where(
                Run.parent_run_id == run.parent_run_id, Run.sequence == run.sequence - 1
            )
        )
    ).scalars().first()
    if prev is None or not isinstance(prev.result, dict):
        return None
    answer = prev.result.get("answer")
    return answer if isinstance(answer, str) and answer.strip() else None


async def _finalize_parent(
    db, parent_id: str, *, failed: bool, error: str | None, stages: list[Run]
) -> None:
    """收敛 pipeline 父 Run（幂等：仅在 queued 时处理）。"""
    parent = await db.get(Run, parent_id)
    if parent is None or parent.status != "queued":
        return

    transition(parent, RunStatus.RUNNING)
    if failed:
        parent.error = f"pipeline 中断：{error or '某阶段执行失败'}"
        transition(parent, RunStatus.FAILED)
        parent.result = {"strategy": "pipeline", "note": "链式中断"}
        await db.commit()
        run_events.run_failed(parent.id, parent.error)
        return

    transition(parent, RunStatus.COMPLETED)
    parent.result = {
        "strategy": "pipeline",
        "stages": [
            {
                "sequence": stage.sequence,
                "run_id": stage.id,
                "status": stage.status,
                "cost_cents": stage.cost_cents,
            }
            for stage in sorted(stages, key=lambda r: r.sequence)
        ],
        "note": "pipeline 各阶段依次完成",
    }
    await db.commit()
    run_events.run_completed(parent.id, parent.result)


async def _advance_pipeline(db, run: Run) -> None:
    """pipeline 收尾：完成则触发下一阶段，失败则取消剩余阶段并收敛父 Run。"""
    if run.status == "needs_input":
        return  # 挂起等回复；恢复执行完成后会再次触发推进

    parent_id = run.parent_run_id
    if not parent_id:
        return

    siblings = list(
        (await db.execute(select(Run).where(Run.parent_run_id == run.parent_run_id)))
        .scalars()
        .all()
    )
    next_stage = next((r for r in siblings if r.sequence == run.sequence + 1), None)

    if run.status == "completed" and next_stage is not None:
        if next_stage.status == "queued":
            run_events.log_line(
                run.id, f"第 {run.sequence} 阶段完成，触发第 {next_stage.sequence} 阶段"
            )
            _enqueue([next_stage.id])
        return

    if run.status != "completed":
        cancelled = 0
        for pending in siblings:
            if pending.sequence > run.sequence and pending.status == "queued":
                transition(pending, RunStatus.CANCELLED)
                cancelled += 1
        if cancelled:
            await db.commit()
            run_events.log_line(
                run.id, f"第 {run.sequence} 阶段未成功，已取消剩余 {cancelled} 个阶段"
            )
        await _finalize_parent(db, parent_id, failed=True, error=run.error, stages=siblings)
        return

    # 最后一阶段完成：收敛父 Run
    await _finalize_parent(db, parent_id, failed=False, error=None, stages=siblings)


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


async def _is_cancelled(db, run_id: str) -> bool:
    """Read committed state, never a stale object from the long-lived Worker session."""
    status = await db.scalar(select(Run.status).where(Run.id == run_id))
    return status == "cancelled"


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

    # 组父 Run 处理：
    # - router：父 Run 自己执行（先用 LLM 选成员，再按该成员配置跑）
    # - fan_out：占位完成（子 Run 各自并发执行）
    # - pipeline：父 Run 不入队（由最后一阶段收敛）；若被误入队则跳过不改状态
    routing: dict | None = None
    if run.assignee_type == "group":
        if run.strategy == "router":
            decision = await route(
                db, group_id=run.assignee_id, request_text=_request_text(request)
            )
            if decision is None:
                run.error = "组内无可用成员，无法路由"
                transition(run, RunStatus.RUNNING)
                transition(run, RunStatus.FAILED)
                await db.commit()
                run_events.run_failed(run.id, run.error)
                return
            # 路由决策也是一次 LLM 调用，成本计入本 Run（不能消失在账外）
            if decision.cost_cents:
                run.cost_cents += decision.cost_cents
                await db.commit()
                run_events.cost_update(run.id, run.cost_cents)
            agent = decision.agent
            routing = {
                "strategy": "router",
                "selected_agent_id": agent.id,
                "selected_agent_name": agent.name,
                "reason": decision.reason,
                "cost_cents": decision.cost_cents,
            }
            run_events.log_line(run.id, f"路由选择：{agent.name}（{decision.reason}）")
            logger.info(
                "router_selected", run_id=_run_id_for_log(run), agent_id=agent.id[:8]
            )
        elif run.strategy == "pipeline":
            logger.warning(
                "pipeline_parent_should_not_be_queued", run_id=_run_id_for_log(run)
            )
            return
        else:
            # fan_out 父 Run：仅做编排占位（子 Run 各自执行）。
            # 状态机要求 queued → running → completed：先迁移 running 再 completed。
            transition(run, RunStatus.RUNNING)
            await db.commit()
            run_events.status_change(run.id, run.status)
            transition(run, RunStatus.COMPLETED)
            run.result = {"note": "组父 Run：由子 Run 完成执行"}
            await db.commit()
            run_events.run_completed(run.id, run.result)
            return

    runner = _build_runner(run, agent)

    async def audit_denied(detail: dict) -> None:
        await write_audit(
            db,
            user_id=run.owner_id,
            action="tool_denied",
            target_type="run",
            target_id=run.id,
            detail=detail,
        )
        await db.commit()

    runner.audit_denied = audit_denied
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
        return await _is_cancelled(db, run.id)

    async def persist_sandbox_result(detail: dict) -> None:
        """执行完成时写入沙箱事实、制品和审计，并发布运行详情事件。"""
        if agent is None:
            raise RuntimeError("沙箱执行缺少 Agent 归属")
        step = detail["step"]
        step_id = str(detail["step_id"])
        persisted_step = await db.get(Step, step_id)
        if persisted_step is None:
            raise RuntimeError(f"沙箱步骤 {step_id} 未初始化")
        persisted_step.output = step.get("output")
        persisted_step.duration_ms = step.get("duration_ms", 0)
        persisted_step.status = "failed" if not detail["result"].get("ok", False) else "completed"
        await db.flush()
        payload = detail["payload"]
        result = detail["result"]
        execution = await sandbox_service.persist_sandbox_result(
            db,
            run_id=run.id,
            step_id=step_id,
            agent_id=agent.id,
            user_id=run.owner_id,
            payload=payload,
            result=result,
        )
        egress_records = await egress_service.record_accesses(
            db,
            run_id=run.id,
            execution_id=execution.id,
            agent_id=agent.id,
            user_id=run.owner_id,
            accesses=list(payload.get("egress_accesses") or []),
        )
        for access in egress_records:
            if access.action != "deny":
                continue
            access_detail: dict[str, Any] = {
                "execution_id": execution.id,
                "target_ip": str(access.target_ip),
                "target_domain": access.target_domain or "ip-direct",
                "port": access.port,
                "matched_rule_id": access.matched_rule_id,
            }
            await write_audit(
                db,
                user_id=run.owner_id,
                action="egress_denied",
                target_type="sandbox_execution",
                target_id=execution.id,
                detail=access_detail,
            )
            run_events.egress_denied(
                run.id,
                execution.id,
                target_ip=access_detail["target_ip"],
                target_domain=access_detail["target_domain"],
                port=access_detail["port"],
                matched_rule_id=access_detail["matched_rule_id"],
            )
        await db.commit()
        run_events.sandbox_finished(
            run.id,
            execution.id,
            execution.status,
            execution.exit_code,
            execution.duration_ms,
            artifacts=len(payload.get("artifacts") or []),
        )

    async def publish_sandbox_started(detail: dict) -> None:
        if agent is None:
            raise RuntimeError("沙箱执行缺少 Agent 归属")
        executor = runner.executors.get(str(detail["tool_name"]))
        set_output_callback = getattr(executor, "set_output_callback", None)
        if callable(set_output_callback):
            loop = asyncio.get_running_loop()
            run_id, execution_id = str(run.id), str(detail["execution_id"])

            def publish_output(stream: str, text: str) -> None:
                loop.call_soon_threadsafe(
                    run_events.sandbox_output, run_id, execution_id, stream, text
                )

            set_output_callback(publish_output)
        set_egress_snapshot = getattr(executor, "set_egress_snapshot", None)
        if callable(set_egress_snapshot):
            mode, global_rules, agent_rules = await egress_service.snapshot_for_execution(
                db, agent_id=agent.id
            )
            set_egress_snapshot(
                {
                    "mode": mode,
                    "global_rules": [asdict(rule) for rule in global_rules],
                    "agent_rules": [asdict(rule) for rule in agent_rules],
                }
            )
        step_id = str(detail["step_id"])
        db.add(
            Step(
                id=step_id,
                run_id=run.id,
                seq=int(detail["step_seq"]),
                kind="tool",
                input={"tool_name": detail["tool_name"]},
                output=None,
                tool_name=str(detail["tool_name"]),
                status="running",
            )
        )
        await db.flush()
        await sandbox_service.start_execution(
            db,
            run_id=run.id,
            step_id=step_id,
            agent_id=agent.id,
            language=str(detail["language"]),
            code=str(detail["code"]),
            limits=dict(detail["limits"]),
            execution_id=str(detail["execution_id"]),
        )
        await db.commit()
        run_events.sandbox_started(
            run.id,
            str(detail["execution_id"]),
            int(detail["step_seq"]),
            str(detail["language"]),
            dict(detail["limits"]),
        )

    runner.on_sandbox_started = publish_sandbox_started
    runner.on_sandbox_result = persist_sandbox_result

    # pipeline：非首阶段的输入 = 原始需求 + 上一阶段产出
    request_text = _request_text(request)
    if run.strategy == "pipeline" and run.sequence > 1 and run.parent_run_id:
        previous = await _previous_stage_output(db, run)
        if previous:
            request_text = (
                f"{request_text}\n\n"
                "## 上一阶段产出（请在此基础上继续加工，不要重复已完成的工作）\n"
                f"{previous}"
            )

    try:
        final_status = await runner.run(
            run=run,
            request_text=request_text,
            save_step=save_step,
            publish_event=publish_event,
            set_status=set_status,
            set_cost=set_cost,
            extra_user_messages=extra_messages,
            should_abort=check_cancelled,
        )
        await _persist_steps(db, run, steps)
        await db.commit()
        # router：把路由决策并入结果（前端/审计能看到是谁被选中、为什么）
        if routing:
            run.result = {**(run.result or {}), "routing": routing}
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
    finally:
        # pipeline：无论成功/失败/挂起，都据当前状态推进链路或收敛父 Run
        if run.strategy == "pipeline" and run.parent_run_id:
            try:
                await _advance_pipeline(db, run)
            except Exception:
                logger.exception("pipeline_advance_failed", run_id=_run_id_for_log(run))


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
