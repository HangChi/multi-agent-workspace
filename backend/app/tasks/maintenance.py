"""维护任务（Beat 周期调度）：僵尸 Run 回收。

Worker 崩溃/未启动时 Celery 消息可能长期不被消费：Run 卡在 queued 或 running
永不终结，看板一直显示「执行中」。这里每 5 分钟扫一次，按状态机合法迁移到
failed（终态可重试）并推送 run_failed 事件。
"""

import asyncio
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import or_, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import SessionLocal
from app.models.sandbox_execution import SandboxExecution
from app.services.reaper_service import reap_stale_runs
from app.tasks import events as run_events
from app.tasks.celery_app import celery_app

logger = structlog.get_logger(__name__)
SANDBOX_DATA_RETENTION_DAYS = 90


async def purge_expired_sandbox_data(
    db: AsyncSession, *, now: datetime | None = None
) -> int:
    """Clear code and captured output older than 90 days, retaining execution metadata."""
    cutoff = (now or datetime.now(UTC)) - timedelta(days=SANDBOX_DATA_RETENTION_DAYS)
    result = await db.execute(
        update(SandboxExecution)
        .where(
            SandboxExecution.created_at < cutoff,
            or_(
                SandboxExecution.code != "",
                SandboxExecution.stdout.is_not(None),
                SandboxExecution.stderr.is_not(None),
            ),
        )
        .values(code="", stdout=None, stderr=None)
    )
    return int(getattr(result, "rowcount", 0) or 0)


@celery_app.task(name="app.tasks.maintenance.purge_expired_sandbox_data")
def purge_expired_sandbox_data_task() -> int:
    """Daily maintenance task for expired sandbox source and output."""

    async def _run() -> int:
        async with SessionLocal() as db:
            purged = await purge_expired_sandbox_data(db)
            await db.commit()
            return purged

    purged = asyncio.run(_run())
    if purged:
        logger.info("purge_expired_sandbox_data_done", count=purged)
    return purged


@celery_app.task(name="app.tasks.maintenance.reap_stale_runs")
def reap_stale_runs_task() -> list[str]:
    """回收僵尸 Run（同步任务壳，内部 asyncio.run）。"""

    async def _run() -> list[str]:
        async with SessionLocal() as db:
            reaped = await reap_stale_runs(db)
            await db.commit()
            return reaped

    reaped = asyncio.run(_run())
    if reaped:
        for run_id in reaped:
            try:
                run_events.run_failed(run_id, "执行超时/未被消费，已自动终止（可重试）")
            except Exception as exc:  # noqa: BLE001 - 事件推送失败不应影响回收结果
                logger.warning("reap_event_failed", run_id=run_id[:8], error=str(exc))
        logger.info("reap_stale_runs_done", count=len(reaped))
    return reaped
