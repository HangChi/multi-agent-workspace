"""调度扫描任务（T029）：Beat 每分钟触发一次。

扫描本身在 `schedule_service.run_due_schedules` 里，这里只做三件事：
起一个独立 session、把异步函数跑起来、把结果写进日志。

几处刻意的取舍：

1. **任务里不做 `db.commit()` 收尾**：`run_due_schedules` 内部对每条规则
   各自提交。若在这里再提交一次，会把"某条规则处理到一半"的状态一起提交
   ——而单条失败时必须只回滚那一条。
2. **`schedule_scan_enabled` 的判断放在服务层**，不放在任务层。放在这里
   会让「关掉扫描」只对 Beat 生效，手工调用服务仍会跑；放在服务层则任何
   入口都一致。
3. **不加 `expires`/`time_limit` 到很短**：一次扫描要处理最多
   `schedule_batch_size` 条规则，每条都要建单 + 可能入队。给太紧的超时会导致
   "处理到一半被杀"，留下一批占位了但没建单的记录（这些会表现为丢一次触发，
   虽然按设计可接受，但没有必要人为制造）。
"""

import asyncio

import structlog

from app.core.db import SessionLocal
from app.services.schedule_service import run_due_schedules
from app.tasks.celery_app import celery_app

logger = structlog.get_logger(__name__)


@celery_app.task(name="app.tasks.schedule.scan_due_schedules")
def scan_due_schedules_task() -> int:
    """扫描并处理到期规则（同步任务壳，内部 asyncio.run）。"""

    async def _run() -> int:
        async with SessionLocal() as db:
            return await run_due_schedules(db)

    try:
        processed = asyncio.run(_run())
    except Exception:
        # 未预期异常不能让 Beat 认为任务"正常完成"而静默跳过整分钟——
        # 记录日志后重新抛出，让 Celery 的失败计数可见。
        logger.exception("schedule_scan_failed")
        raise

    if processed:
        logger.info("schedule_scan_done", processed=processed)
    return processed
