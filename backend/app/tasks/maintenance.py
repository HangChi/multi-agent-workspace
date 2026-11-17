"""维护任务（Beat 周期调度）：僵尸 Run 回收。

Worker 崩溃/未启动时 Celery 消息可能长期不被消费：Run 卡在 queued 或 running
永不终结，看板一直显示「执行中」。这里每 5 分钟扫一次，按状态机合法迁移到
failed（终态可重试）并推送 run_failed 事件。
"""

import asyncio
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import or_, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
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


async def prune_schedule_history(
    db: AsyncSession,
    *,
    keep_rows: int | None = None,
    retention_days: int | None = None,
    now: datetime | None = None,
) -> int:
    """清理过期的调度触发历史，返回删除行数。

    两条规则同时生效，取更严格的那个结果：
    - 每条规则最多保留 `keep_rows` 行（按 planned_at 倒序）
    - 早于 `retention_days` 的行删除

    **不动 `schedules` 上的汇总字段**（last_run_at/last_result/
    consecutive_failures）：它们就是为了在历史被清理后仍能回答
    「上次跑成功没有、连续失败几次」。若跟着历史一起清，用户清理完就再也
    看不到规则的当前健康状况了。
    """
    keep = keep_rows if keep_rows is not None else settings.schedule_history_max_rows
    days = (
        retention_days
        if retention_days is not None
        else settings.schedule_history_retention_days
    )
    cutoff = (now or datetime.now(UTC)) - timedelta(days=days)

    # 1) 超过保留行数的部分：用窗口函数按规则分组取序号
    # 用 RETURNING 数删除行数而不是 result.rowcount：asyncpg 下 TextClause
    # 返回的 Result 没有可靠的 rowcount（mypy 也指出来了），RETURNING 是
    # 跨驱动一致的写法。
    over_limit = text(
        """
        DELETE FROM schedule_triggers
        WHERE id IN (
            SELECT id FROM (
                SELECT id, row_number() OVER (
                    PARTITION BY schedule_id ORDER BY planned_at DESC, id DESC
                ) AS rn
                FROM schedule_triggers
            ) ranked
            WHERE rn > :keep
        )
        RETURNING id
        """
    )
    removed = len((await db.execute(over_limit, {"keep": keep})).all())

    # 2) 超过保留天数的
    expired = await db.execute(
        text("DELETE FROM schedule_triggers WHERE planned_at < :cutoff RETURNING id"),
        {"cutoff": cutoff},
    )
    removed += len(expired.all())

    await db.commit()
    return removed


@celery_app.task(name="app.tasks.maintenance.prune_schedule_history")
def prune_schedule_history_task() -> int:
    """每日清理调度触发历史（与沙箱数据清理同批）。"""

    async def _run() -> int:
        async with SessionLocal() as db:
            return await prune_schedule_history(db)

    removed = asyncio.run(_run())
    if removed:
        logger.info("prune_schedule_history_done", removed=removed)
    return removed
