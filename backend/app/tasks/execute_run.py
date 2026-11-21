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
from datetime import UTC, datetime, timedelta
from typing import Any

import redis
import structlog
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.config import settings
from app.core.db import SessionLocal
from app.executors.catalog import CATALOG
from app.executors.llm import LiteLLMClient, LLMClientError
from app.executors.sandbox import build_executors
from app.models.agent import Agent
from app.models.request import Request
from app.models.run import Run
from app.models.step import Step
from app.models.tool import Tool
from app.models.user import new_uuid
from app.orchestrator.retry_policy import (
    backoff_seconds,
    is_retryable,
)
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
from app.services.external_invocation_service import (
    build_external_executors,
    ensure_external_snapshots,
)
from app.services.quota_service import QuotaExceeded, assert_user_quota
from app.services.request_service import settle_request_status
from app.services.tool_service import (
    enabled_tool_specs,
    filter_grants,
    reconcile_agent_grants,
)
from app.tasks import events as run_events
from app.tasks.celery_app import celery_app

logger = structlog.get_logger(__name__)

_LOCK_TTL_SECONDS = 3600  # 与 Agent.max_run_seconds 上限对齐（默认 1800，留余量）


def _acquire_lock(run_id: str) -> str | None:
    """Redis SET NX EX：获取执行锁，成功返回持有者 token，失败返回 None。

    值不是固定 "1" 而是每次唯一：释放时要靠它判断「这把锁还是我的吗」。
    见 `_release_lock` 与 docs/code-audit-2026-11-21.md D6。
    """
    token = new_uuid()
    client = redis.from_url(settings.redis_url, decode_responses=True)
    try:
        ok = client.set(f"run:lock:{run_id}", token, nx=True, ex=_LOCK_TTL_SECONDS)
        return token if ok else None
    finally:
        client.close()


def _release_lock(run_id: str, token: str | None) -> None:
    """只释放自己持有的锁（compare-and-delete）。

    无条件 `DELETE` 有个低概率但后果重的漏洞：Worker 被 SIGKILL 卡过 TTL
    （3600s）后恢复，此时锁已被别的 Worker 重新获取，它却会把**别人的锁**
    删掉 → 第三个 Worker 又能拿到 → 同一 Run 并发执行（seq 唯一约束冲突、
    成本重复累加）。用与 `sandbox_service.release_concurrency_slot` 相同的
    Lua CAS 片段即可。
    """
    if token is None:
        return
    client = redis.from_url(settings.redis_url, decode_responses=True)
    try:
        client.eval(
            "if redis.call('get', KEYS[1]) == ARGV[1] "
            "then return redis.call('del', KEYS[1]) else return 0 end",
            1,
            f"run:lock:{run_id}",
            token,
        )
    finally:
        client.close()


async def _load_context(db, run_id: str) -> tuple[Run, Agent | None, Request, int]:
    """加载 Run + Agent（按 assignee_id）+ Request，并返回续写起点 seq。

    刻意**不** selectinload(Run.steps)：下游从不读 run.steps（续写序号靠
    这里顺带查的 max(seq)），而历史 step 的 output 可能含大段 sandbox stdout。
    重试/恢复一个跑了多步的 Run，每次都会把它们全量反序列化进 identity map，
    纯属浪费。见 docs/code-audit-2026-11-21.md P3。
    """
    run = (await db.execute(select(Run).where(Run.id == run_id))).scalars().first()
    if run is None:
        raise RuntimeError(f"Run {run_id} 不存在")

    agent: Agent | None = None
    if run.assignee_type == "agent":
        agent = await db.get(Agent, run.assignee_id)

    request = await db.get(Request, run.request_id)
    if request is None:
        raise RuntimeError(f"Request {run.request_id} 不存在")

    # 续写起点（FR-021）：与上下文一次取回，避免 _execute 里再发一条查询
    last_seq = int(
        (
            await db.execute(select(func.max(Step.seq)).where(Step.run_id == run.id))
        ).scalar()
        or 0
    )

    return run, agent, request, last_seq


def _build_runner(
    run: Run,
    agent: Agent | None,
    *,
    tool_names: list[str] | None = None,
    abort_check=None,
    on_sandbox_result=None,
    on_external_started=None,
    external_executors: dict[str, Any] | None = None,
) -> ReActRunner:
    """按 Agent 配置构造 runner；无 Agent（组父 Run）用默认配置。"""
    if agent is not None:
        executors = build_executors(tool_names or [], abort_check=abort_check)
        executors.update(external_executors or {})
        return ReActRunner(
            llm=LiteLLMClient(model=agent.model),
            executors=executors,
            max_steps=agent.max_steps,
            timeout_seconds=agent.max_run_seconds,
            cost_limit_cents=agent.cost_limit_cents,
            system_prompt=agent.system_prompt,
            on_sandbox_result=on_sandbox_result,
            on_external_started=on_external_started,
        )
    # 组父 Run：仅做编排占位（fan_out 时子 Run 各自执行），直接完成不调 LLM
    return ReActRunner(
        llm=LiteLLMClient(),
        executors=build_executors([]),
    )


async def _persist_steps(db, run: Run, steps: list[dict]) -> None:
    """批量写 steps 表（seq 唯一约束 per run）。

    步骤分两类，只有第二类需要查库（见 docs/code-audit-2026-11-21.md P2）：

    - **不带 id**：runner 现场生成的普通步骤（LLM / final / 未走预建路径的工具
      调用），它必然是新行，直接 `add` 即可。此前对这类也逐条 `db.get()`，
      纯属白跑往返——`db.get` 虽命中 identity map，但首次访问仍是独立 SELECT。
    - **带 id**：沙箱执行与外部工具调用会预建一条 `running` 行（子表以
      `step_id` 为外键），runner 事后回填。这批用一次 `IN` 取回而不是逐条查。
    """
    if not steps:
        return

    precreated_ids: list[str] = []
    for step in steps:
        if step.get("id"):
            precreated_ids.append(step["id"])
        else:
            # Keep the identity in the runner's step dict so subsequent batch flushes
            # reuse an already persisted step rather than duplicating its sequence.
            step["id"] = new_uuid()

    existing: dict[str, Step] = {}
    if precreated_ids:
        persisted_rows = (
            (await db.execute(select(Step).where(Step.id.in_(precreated_ids))))
            .scalars()
            .all()
        )
        # 注意：身份映射里已有的行也会出现在这里，所以只需按 id 建索引即可
        existing = {row.id: row for row in persisted_rows}

    for step in steps:
        persisted = existing.get(step["id"])
        if persisted is not None:
            # A step pre-created as "running" so a child row could reference it
            # (sandbox execution, external tool invocation) is filled in here with
            # the state the runner reports. A step a specialized handler already
            # finalized keeps that outcome: the sandbox handler decides its status
            # from the execution result, which the runner's step dict cannot know.
            if persisted.status == "running":
                persisted.seq = step["seq"]
                persisted.input = step.get("input")
                persisted.output = step.get("output")
                persisted.tool_name = step.get("tool_name")
                persisted.duration_ms = step.get("duration_ms", 0)
                persisted.cost_cents = step.get("cost_cents", 0)
                persisted.status = step.get("status", "completed")
            continue
        row = Step(
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
        db.add(row)
        existing[row.id] = row  # 同批内重复 id 时第二次走 update 分支而不是再 add
    await db.flush()


def _enqueue(run_ids: list[str]) -> None:
    """把 Run 入队（pipeline 链式推进用）。失败只告警，不影响当前执行结果。"""
    for run_id in run_ids:
        try:
            celery_app.send_task("app.tasks.execute_run.execute_run", args=[run_id])
        except (ConnectionError, OSError, ValueError) as exc:
            logger.warning("celery_enqueue_failed", run_id=run_id, error=str(exc))


def _enqueue_with_delay(run_id: str, delay_seconds: float) -> None:
    """延迟入队（006 自动重试）。

    用 `countdown` 而不是 Celery 的 `self.retry()`：见
    `specs/006-run-retry/research.md` R-01——后者会重新抛出异常、让
    `acks_late` 的消息在重试窗口内再次投递，与本次调度形成两个执行体，
    正是要防的重复执行。这里投递的是一个**全新任务**，靠 Redis 锁保证
    同一时刻只有一个执行体。

    失败只告警： broker 不可用时 Run 已落在 `retrying` + `next_retry_at`，
    reaper 不会回收它（FR-012），用户可手动重试——不会静默丢失。
    """
    try:
        celery_app.send_task(
            "app.tasks.execute_run.execute_run",
            args=[run_id],
            countdown=max(0.0, delay_seconds),
        )
    except (ConnectionError, OSError, ValueError) as exc:
        logger.warning("celery_retry_enqueue_failed", run_id=run_id, error=str(exc))


async def _previous_stage_output(db, run: Run) -> str | None:
    """pipeline：取上一阶段的产出文本（result.answer）。"""
    prev = (
        (
            await db.execute(
                select(Run).where(
                    Run.parent_run_id == run.parent_run_id,
                    Run.sequence == run.sequence - 1,
                )
            )
        )
        .scalars()
        .first()
    )
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

    # 006：阶段进入重试等待时，链路保持原样——它还会回来，既不取消剩余阶段，
    # 也不把父 Run 收敛成 failed（那会在瞬时抖动时误杀整条 pipeline）。
    if run.status == "retrying":
        run_events.log_line(run.id, f"第 {run.sequence} 阶段将自动重试，链路暂停等待")
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
        await _finalize_parent(
            db, parent_id, failed=True, error=run.error, stages=siblings
        )
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


async def _fail_run(
    db: AsyncSession,
    run: Run,
    error: str,
    *,
    settle: bool,
    emit_to: str | None = None,
) -> None:
    """把一个 Run 标成失败并落库（R4）。

    原先这段在本文件里出现 6 次，逐字相同的部分有四句：

        run.error = ...; transition(run, RunStatus.FAILED)
        await db.commit(); run_events.run_failed(<id>, run.error)

    两个参数保留了两处**真实差异**，不是随手加的开关：

    - `settle`：落库后要不要收敛需求状态（`finally` 与配额兜底那两处要）。
      早失败路径（需求行不存在、组内无可用成员）不收敛——需求行本身就是问题，
      再去 settle 只会再查一次空。
      **刻意不给默认值**：漏传时应该报 TypeError，而不是静默按某一侧生效——
      这两种选择都会在某个路径上多收敛或少收敛一次，且都不会被测试抓到。
    - `emit_to`：事件用哪个 id。绝大多数传 `run.id`，但 `execute_run` 外层
      的采集闭包里 `run` 是另一个 session 重新 `db.get` 出来的对象，那里用
      闭包捕获的 `run_id` 更直接。两种写法语义相同，改掉任何一个都属于
      「为了整齐而动行为」，不做。

    与 `runner._settle` 是**两套 sink**：runner 通过注入的 `publish_event`
    异步回调发事件（可测、无 Redis 依赖）；这里走 `run_events.*` 的同步单例
    客户端（Worker 进程直发）。两者的形状相似但归属不同层，没有合并。
    """
    run.error = error
    transition(run, RunStatus.FAILED)
    await db.commit()
    run_events.run_failed(emit_to or run.id, run.error)
    if settle and await settle_request_status(db, run.request_id):
        await db.commit()


async def _is_cancelled(db, run_id: str) -> bool:
    """Read committed state, never a stale object from the long-lived Worker session."""
    status = await db.scalar(select(Run.status).where(Run.id == run_id))
    return status == "cancelled"


async def _schedule_auto_retry(db, run: Run, exc: BaseException) -> bool:
    """瞬时失败时安排一次自动重试（006）。

    返回 True 表示「已转入 retrying，调用方不要再标 failed」。

    做的事（对应 spec FR-006..009 / FR-016..019）：
    1. 分类失败：仅瞬时类且未到上限才重试（retry_policy.is_retryable）
    2. 记录历史 + 计划时刻 + 迁移到 `retrying`
    3. 发 WS 事件 + 写审计
    4. 用 `countdown` 投递一个全新任务（不用 self.retry()，见 research R-01）

    成本**不在此处触碰**：`run.cost_micros` 由 runner 增量累加，重试路径
    不清零即自然形成跨次累计（research R-04）。

    状态校验（FR-011）不在这里做——它属于「消费时」的防线，见
    `execute_run` 里的 `_should_skip_execution`。
    """
    # 无论是否重试，这次失败都要进历史（FR-009：耗尽后仍要能看到每次败在哪）
    run.record_failure(str(exc))

    max_attempts = settings.run_max_attempts
    if not is_retryable(exc, run.attempt or 1, max_attempts):
        await db.commit()
        return False

    delay = backoff_seconds(
        run.attempt or 1,
        settings.run_retry_base_seconds,
        settings.run_retry_cap_seconds,
    )
    now = datetime.now(UTC)
    run.next_retry_at = now + timedelta(seconds=delay)

    transition(run, RunStatus.RETRYING)
    await db.commit()

    run_events.run_retrying(
        run.id,
        attempt=(run.attempt or 1) + 1,
        max_attempts=max_attempts,
        wait_seconds=delay,
        error=str(exc),
        next_retry_at=run.next_retry_at.isoformat(),
    )
    run_events.log_line(
        run.id,
        f"第 {run.attempt} 次执行失败（{str(exc)[:80]}），"
        f"{delay:.1f}s 后自动重试（第 {(run.attempt or 1) + 1}/{max_attempts} 次）",
        level="warning",
    )
    try:
        await write_audit(
            db,
            user_id=run.owner_id,
            action="run_auto_retry",
            target_type="run",
            target_id=run.id,
            detail={
                "from_status": "running",
                "attempt": run.attempt or 1,
                "max_attempts": max_attempts,
                "wait_seconds": round(delay, 2),
                "error": str(exc)[:500],
            },
            strict=True,
        )
        await db.commit()
    except Exception:
        # 审计失败不能拦住重试：调度已经决定了，回滚比丢掉一次重试更糟
        logger.exception("auto_retry_audit_failed", run_id=_run_id_for_log(run))

    _enqueue_with_delay(run.id, delay)
    return True


async def _execute(
    db, run: Run, agent: Agent | None, *, last_seq_hint: int = 0
) -> None:
    """真正的执行链：状态机 → runner → 持久化。

    `last_seq_hint`：`_load_context` 顺带查到的历史最大 seq（FR-021 续写起点）。
    传 0 时本地再查一次，保持单独调用本函数时的正确性。
    """
    # 已取消/终态：不再执行（Worker 发现取消直接跳过）
    if run.status == "cancelled":
        logger.info("run_cancelled_before_exec", run_id=_run_id_for_log(run))
        return

    request = await db.get(Request, run.request_id)
    if request is None:
        await _fail_run(db, run, "需求不存在，无法执行", settle=False)
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
                # 状态机要求 queued → running → failed，先迁移再终结
                transition(run, RunStatus.RUNNING)
                await _fail_run(db, run, "组内无可用成员，无法路由", settle=False)
                return
            # 路由决策也是一次 LLM 调用，成本计入本 Run（不能消失在账外）
            if decision.cost_cents or decision.cost_micros:
                run.cost_micros = (run.cost_micros or 0) + decision.cost_micros
                run.cost_cents = round(run.cost_micros / 1_000_000)
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

    enabled = [spec.name for spec in await enabled_tool_specs(db)]
    tool_names: list[str] = []
    if agent is not None:
        grants = reconcile_agent_grants(agent, enabled)
        if run.tool_grants_snapshot is None:
            # Router selection / unexecuted legacy runs: this is the first point
            # where the actual configuration is known. Completed legacy history
            # is never backfilled from today's Agent settings.
            run.tool_grants_snapshot = list(grants)
        tool_names = filter_grants(run.tool_grants_snapshot, grants)
        await db.flush()
        if run.strategy == "router":
            await ensure_external_snapshots(
                db, run_id=run.id, owner_id=run.owner_id, agent_id=agent.id
            )

    async def external_abort_check() -> bool:
        return await _is_cancelled(db, run.id)

    external_executors = await build_external_executors(
        db, run_id=run.id, owner_id=run.owner_id, abort_check=external_abort_check
    )

    async def publish_external_started(detail: dict) -> None:
        """外部工具调用前先落 step 行。

        调用记录（external_tool_invocations）以 step_id 为外键，而执行器是在
        调用过程中写入该记录的，所以步骤行必须先于执行存在——与沙箱路径
        ``publish_sandbox_started`` 的顺序一致。终态由 runner 通过
        ``save_step`` 回填（见 ``_persist_steps``）。
        """
        db.add(
            Step(
                id=str(detail["step_id"]),
                run_id=run.id,
                seq=int(detail["step_seq"]),
                kind="tool",
                input={"tool_name": detail["stable_name"]},
                output=None,
                tool_name=str(detail["stable_name"]),
                status="running",
            )
        )
        await db.flush()
        await db.commit()

    runner = _build_runner(
        run,
        agent,
        tool_names=tool_names,
        external_executors=external_executors,
        on_external_started=publish_external_started,
    )

    async def authorize_tool(name: str) -> bool:
        if name in external_executors:
            return (
                True  # Per-call current-state check runs inside the external executor.
            )
        if agent is None:
            return False
        # Query scalar columns so operator disables and Agent edits committed
        # during an LLM call are visible despite this long-lived ORM session.
        current_rows = (
            await db.execute(
                select(Agent.tools, Tool.name)
                .select_from(Agent)
                .outerjoin(
                    Tool,
                    and_(Tool.enabled.is_(True), Tool.name.in_(CATALOG)),
                )
                .where(Agent.id == agent.id)
            )
        ).all()
        current_grants = current_rows[0][0] if current_rows else []
        current_enabled = [row[1] for row in current_rows if row[1] is not None]
        return name in (run.tool_grants_snapshot or []) and name in filter_grants(
            current_grants, current_enabled
        )

    runner.authorize_tool = authorize_tool

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

    # FR-021 恢复执行（needs_input → running）：历史步骤已经写库，重跑的新步骤从
    # 最大 seq 之后续写，而不是把它们删掉。删除会让审计丢失整段执行过程，也会让
    # external_tool_invocations.step_id / sandbox_executions.step_id 指向不存在的行，
    # 运行详情里就会出现悬挂的调用记录。
    #
    # 生产路径由 _load_context 顺带查好（last_seq_hint），避免这里再发一条查询。
    last_seq = last_seq_hint or int(
        (
            await db.execute(select(func.max(Step.seq)).where(Step.run_id == run.id))
        ).scalar()
        or 0
    )
    if last_seq:
        logger.info(
            "run_resume_continue_seq", run_id=_run_id_for_log(run), from_seq=last_seq
        )
    user_reply = run.user_reply
    extra_messages = [user_reply] if user_reply else None
    if user_reply:
        # 消费即清空：否则再次进入 needs_input 时同一句回复会被重新注入一次，
        # 模型会把旧回答当成新补充信息再执行一遍。
        run.user_reply = None
        await db.commit()

    async def save_step(step: dict) -> None:
        # Persist the step and progress together BEFORE status/events can expose
        # it. A terminal run must never be visible with an empty/partial history.
        await _persist_steps(db, run, [step])
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
        persisted_step.status = (
            "failed" if not detail["result"].get("ok", False) else "completed"
        )
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
            run_id, execution_id = str(run.id), str(detail["execution_id"])

            def publish_output(stream: str, text: str) -> None:
                # 事件发布已是同步单例客户端（线程安全），沙箱读取线程可直接调用：
                # 不再经 loop.call_soon_threadsafe 绕一圈，也就不会阻塞事件循环。
                run_events.sandbox_output(run_id, execution_id, stream, text)

            set_output_callback(publish_output)
        set_egress_snapshot = getattr(executor, "set_egress_snapshot", None)
        if callable(set_egress_snapshot):
            (
                mode,
                global_rules,
                agent_rules,
            ) = await egress_service.snapshot_for_execution(db, agent_id=agent.id)
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
            initial_seq=last_seq,
        )
        # 不做整批补写：每个 step 都已在 save_step 里当场落库并提交（见那里
        # 的注释——终态可见时历史必须已完整）。此处只需提交 run 自身的字段变更
        # （result / status / cost），`steps` 列表只是内存副本。
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
        await db.commit()
        run_events.needs_input(run.id, str(exc))
    except (MaxStepsExceeded, CostLimitExceeded, ExecutionTimeout) as exc:
        await db.commit()
        run_events.run_failed(run.id, run.error or str(exc))
    except LLMClientError as exc:
        # 006：瞬时类失败先安排自动重试；不可重试才走终态
        if await _schedule_auto_retry(db, run, exc):
            return
        # settle=False：下面 finally 会统一收敛需求状态，这里不能抢先做
        await _fail_run(db, run, str(exc), settle=False)
    except Exception as exc:  # 兜底：任何异常都转为可读失败（FR-017）
        logger.exception("execute_run_unexpected", run_id=_run_id_for_log(run))
        # 006：同上。未知异常按业务类处理（research R-05），因此这里
        # 绝大多数情况会走到终态；保留入口是为了连接层异常（OSError 等）
        # 也能享受自动重试。
        if await _schedule_auto_retry(db, run, exc):
            return
        await _fail_run(db, run, f"执行异常: {exc}", settle=False)
    finally:
        # pipeline：无论成功/失败/挂起，都据当前状态推进链路或收敛父 Run
        if run.strategy == "pipeline" and run.parent_run_id:
            try:
                await _advance_pipeline(db, run)
            except Exception:
                logger.exception("pipeline_advance_failed", run_id=_run_id_for_log(run))
        # 需求状态收敛：分配时置为 running，终态必须由这里推出去，否则看板永远停在
        # 「执行中」。放在 finally 里，失败/取消/超时同样收口。
        try:
            # 006：重试等待中不收敛需求状态——它还没失败，看板该继续显示执行中
            if run.status != RunStatus.RETRYING.value and await settle_request_status(
                db, run.request_id
            ):
                await db.commit()
        except Exception:
            logger.exception("request_settle_failed", run_id=_run_id_for_log(run))


@celery_app.task(name="app.tasks.execute_run.execute_run", bind=True, max_retries=2)
def execute_run(self, run_id: str) -> str:
    """Worker 消费：执行一个 Run（同步 def，内部 async 驱动）。

    Returns:
        str: 最终状态（completed/failed/timeout/cancelled/needs_input/skipped）
    """
    if os.getenv("TESTING") == "1":
        # 测试环境短路：不连 broker/DB，直接返回（集成测试自行 mock 执行链）
        return "skipped-test"

    lock_token = _acquire_lock(run_id)
    if lock_token is None:
        logger.warning("run_lock_busy", run_id=run_id)
        return "busy"

    async def _main() -> str:
        async with SessionLocal() as db:
            run, agent, _, last_seq = await _load_context(db, run_id)
            if run.status == "cancelled":
                return "cancelled"
            # 006 三道防线的第二道（FR-011）：终态后延迟的重试消息到点时丢弃。
            # 判据用 status 而不是 next_retry_at——后者可能因时区/写入顺序偏差，
            # 而 status 是状态机保证的唯一真相。
            if run.status not in {"queued", "running", "retrying"}:
                logger.info(
                    "run_retry_discarded",
                    run_id=run_id,
                    status=run.status,
                    reason="终态后到点的重试消息",
                )
                return f"discarded-{run.status}"
            # 006：重试到点 → 递增 attempt 并进入 running。
            # 在这里而不是调度时递增：attempt 的语义是「正在跑第几次」，
            # 调度时递增会让「等待中的那次」也被算成一次尝试。
            if run.status == "retrying":
                run.bump_attempt()
                run.next_retry_at = None
                transition(run, RunStatus.RUNNING)
                await db.commit()
                run_events.log_line(run_id, f"第 {run.attempt} 次尝试开始（自动重试）")
            # T077 配额兜底：Worker 消费前再校验一次（分配后配额可能已变化）。
            # 已入队任务不因配额变化回滚（软约束），但防止越过配额执行。
            try:
                await assert_user_quota(db, run.owner_id)
            except QuotaExceeded:
                # settle=True：需求已分配给本 Run，拒绝执行也必须把它收口成 failed，
                # 否则需求会永久停在 running 等一个永远不会完成的任务。
                await _fail_run(
                    db, run, "月配额已用尽，任务被拒绝执行", settle=True, emit_to=run_id
                )
                return "failed"
            await _execute(db, run, agent, last_seq_hint=last_seq)
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
                    # 状态机要求 queued → running → failed；若仍在 queued（任务消费即崩），
                    # 先合法迁移到 running 再 failed，避免 TransitionError
                    if run.status == "queued":
                        transition(run, RunStatus.RUNNING)
                    # settle=True：致命错误是终态，需求必须跟着收口。
                    await _fail_run(
                        db,
                        run,
                        f"执行器致命错误: {error_text}",
                        settle=True,
                        emit_to=run_id,
                    )

        asyncio.run(_fail())
        return "failed"
    finally:
        _release_lock(run_id, lock_token)
