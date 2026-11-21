"""僵尸 Run 回收（Beat 周期任务）。

问题：Celery 开着 acks_late，但 Worker 崩溃/未启动时消息可能长期不被消费，
Run 会永远停在 queued；执行中被 kill 则永远停在 running。用户界面表现为
看板卡片卡在「执行中」、详情页状态永不终结。

这里按状态机合法迁移到 failed（终态可一键重试），并把关联需求从 running
回收为 failed，避免看板永久卡住。

阈值：
- queued 超过 `stale_queued_minutes`（默认 30 分钟）
- running 超过 `stale_running_minutes`（默认 90 分钟，大于 max_run_seconds 上限）
"""

from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.request import Request
from app.models.run import Run
from app.orchestrator.request_state_machine import RequestStatus
from app.orchestrator.state_machine import ACTIVE_RUN_STATUSES, RunStatus, transition

logger = structlog.get_logger(__name__)

# 非终态：只要还有这些状态的 Run，需求就不该被「回收成 failed」。
#
# R3：改为引用状态机导出的 `ACTIVE_RUN_STATUSES`（= 全部非终态，含 `retrying`）。
# 这里有个容易混淆的区分，务必看清：
#
# - **「Run 还没结束」**（本集合的语义）：`retrying` 明确属于此类——它在等
#   `next_retry_at` 到点，既没卡住也没失败。
# - **「哪些 Run 该被 reaper 判为僵尸」**：`retrying` 明确不属于此类
#   （006 FR-012）。它不该被**选中**回收，但必须算作「仍然活跃」，
#   否则「该需求下已无活跃 Run」的判断会误判。
#
# 同一份集合原来把这两件事混在一起：字面量 `{queued, running, needs_input}`
# 既漏了 `retrying`（于是重试等待中的 Run 被当成「已无活跃」），又靠注释
# 声称「刻意不含 retrying」。现在拆开——选中逻辑看 `_REAPABLE_QUEUED/RUNNING`，
# 活跃判定看 `ACTIVE_RUN_STATUSES`。
#
# 选中条件（见 `reap_stale_runs` 的 WHERE）本身就只有 queued/running 两种，
# 它从来没机会选中 retrying；真正的问题是「活跃集合漏了它」。
_REAPABLE_QUEUED = RunStatus.QUEUED.value
_REAPABLE_RUNNING = RunStatus.RUNNING.value


async def reap_stale_runs(
    db: AsyncSession,
    *,
    queued_minutes: int | None = None,
    running_minutes: int | None = None,
    now: datetime | None = None,
) -> list[str]:
    """扫描并回收僵尸 Run，返回被回收的 run_id 列表。

    Args:
        queued_minutes: queued 超时阈值（默认取配置）
        running_minutes: running 超时阈值（默认取配置）
        now: 当前时间（测试注入用）

    Returns:
        被标记为 failed 的 run_id 列表
    """
    now = now or datetime.now(UTC)
    queued_before = now - timedelta(
        minutes=queued_minutes
        if queued_minutes is not None
        else settings.stale_queued_minutes
    )
    running_before = now - timedelta(
        minutes=running_minutes
        if running_minutes is not None
        else settings.stale_running_minutes
    )

    stmt = select(Run).where(
        or_(
            and_(
                Run.status == _REAPABLE_QUEUED,
                Run.created_at < queued_before,
            ),
            and_(
                Run.status == _REAPABLE_RUNNING,
                func.coalesce(Run.started_at, Run.created_at) < running_before,
            ),
        )
    )
    stale = list((await db.execute(stmt)).scalars().all())
    if not stale:
        return []

    reaped: list[str] = []
    for run in stale:
        from_queued = run.status == "queued"
        # 状态机只允许 queued → running → failed，不能一步到 failed
        if from_queued:
            transition(run, RunStatus.RUNNING)
        transition(run, RunStatus.FAILED)
        run.error = (
            "排队超时：超过 "
            f"{queued_minutes if queued_minutes is not None else settings.stale_queued_minutes} "
            "分钟未被 Worker 消费，已自动终止（可重试）"
            if from_queued
            else "执行超时：Worker 无进展已超过 "
            f"{running_minutes if running_minutes is not None else settings.stale_running_minutes} "
            "分钟，已自动终止（可重试）"
        )
        reaped.append(run.id)
        logger.warning(
            "stale_run_reaped",
            run_id=run.id[:8],
            from_status="queued" if from_queued else "running",
        )

    await db.flush()

    # 关联需求回收：该需求下已无任何活跃 Run 时，running → failed
    for request_id in {run.request_id for run in stale}:
        active = (
            await db.execute(
                select(func.count())
                .select_from(Run)
                .where(
                    Run.request_id == request_id,
                    Run.status.in_(ACTIVE_RUN_STATUSES),
                )
            )
        ).scalar_one()
        if active:
            continue
        request = await db.get(Request, request_id)
        if request is not None and request.status == RequestStatus.RUNNING.value:
            request.status = "failed"
            logger.warning("stale_request_recovered", request_id=request_id[:8])

    return reaped
