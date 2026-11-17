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
from app.orchestrator.state_machine import RunStatus, transition

logger = structlog.get_logger(__name__)

# 非终态：只要还有这些状态的 Run，需求就不该被回收
_ACTIVE_STATUSES = ("queued", "running", "needs_input")


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
            and_(Run.status == "queued", Run.created_at < queued_before),
            and_(
                Run.status == "running",
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
                .where(Run.request_id == request_id, Run.status.in_(_ACTIVE_STATUSES))
            )
        ).scalar_one()
        if active:
            continue
        request = await db.get(Request, request_id)
        if request is not None and request.status == "running":
            request.status = "failed"
            logger.warning("stale_request_recovered", request_id=request_id[:8])

    return reaped
