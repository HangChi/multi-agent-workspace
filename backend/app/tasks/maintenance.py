"""维护任务（Beat 周期调度）：僵尸 Run 回收。

Worker 崩溃/未启动时 Celery 消息可能长期不被消费：Run 卡在 queued 或 running
永不终结，看板一直显示「执行中」。这里每 5 分钟扫一次，按状态机合法迁移到
failed（终态可重试）并推送 run_failed 事件。
"""

import asyncio

import structlog

from app.core.db import SessionLocal
from app.services.reaper_service import reap_stale_runs
from app.tasks import events as run_events
from app.tasks.celery_app import celery_app

logger = structlog.get_logger(__name__)


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
