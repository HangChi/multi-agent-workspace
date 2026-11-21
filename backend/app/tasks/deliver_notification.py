"""外部通知投递任务（018）。

为什么是「任务只做一次尝试」而不是在任务里循环重试：
Celery 的 `self.retry(countdown=...)` 会把消息重新排队并**释放 worker**；
在任务里 `time.sleep(30)` 重试会白占一个 worker 半分钟，通知一多就把队列堵死。

重试策略在 service（`attempt_delivery`）里决定：4xx 直接终态，5xx/超时/429 才要求
重试，退避 2s/8s/30s（共 3 次尝试）。任务只负责「按它说的等」。
"""

from __future__ import annotations

import asyncio
import os

import structlog
from celery.exceptions import MaxRetriesExceededError

from app.core.db import SessionLocal
from app.services import notification_channel_service
from app.tasks.celery_app import celery_app

logger = structlog.get_logger(__name__)


@celery_app.task(
    bind=True,
    name="app.tasks.deliver_notification.deliver_notification",
    # 任务级 max_retries 与 service 的 _MAX_ATTEMPTS 对齐：首次 + 2 次重试 = 3 次尝试
    max_retries=2,
)
def deliver_notification(self, delivery_id: str) -> str:
    """投递一条外部通知（同步任务壳，内部 asyncio.run）。"""
    if os.getenv("TESTING") == "1":
        # 与 execute_run 同款短路：测试环境不连 broker / DB
        return "skipped-test"

    async def _main() -> tuple[str, int]:
        async with SessionLocal() as db:
            outcome, backoff = await notification_channel_service.attempt_delivery(
                db, delivery_id
            )
            await db.commit()
            return outcome, backoff

    outcome, backoff = asyncio.run(_main())
    if outcome in (
        notification_channel_service.DeliveryOutcome.RETRY,
        notification_channel_service.DeliveryOutcome.MISSING,
    ):
        try:
            # self.retry 正常路径抛的是 Retry（Celery 内部异常）——**不能**用
            # `except Exception` 包，那会把重试机制本身吞掉。只兜「次数用尽」。
            raise self.retry(countdown=backoff or 5)
        except MaxRetriesExceededError:
            logger.warning(
                "notification_delivery_retries_exhausted", delivery_id=delivery_id
            )
            return "exhausted"
    return outcome
