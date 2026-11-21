"""前置完成后的自动启动（021）。

为什么是独立任务而不是在 settle 事务里直接 assign：settle 是「终态收敛」的
唯一汇聚点，在里面建 Run + 投递 Celery 会把事务拖长，而且 assign 需要
commit 之后才能入队（否则 worker 读到不存在的 Run）。交给任务：独立事务、
失败可重试、settle 只管发一条消息。

幂等靠三道校验（任何一道不满足就跳过）：
1. 需求还在、`auto_start` 仍开着、`auto_agent_id` 仍设着（用户可能中途关掉）；
2. 前置仍全部终态（`is_blocked`）——避免「解锁后又加了新前置」；
3. `assign` 自己的状态机与配额门禁（会在 pending/queued 之外直接报错）。
"""

from __future__ import annotations

import asyncio
import os

import structlog

from app.api.errors import AppError
from app.core.db import SessionLocal
from app.models.request import Request
from app.services import assignment_service, dependency_service
from app.tasks.celery_app import celery_app

logger = structlog.get_logger(__name__)


@celery_app.task(
    bind=True, name="app.tasks.auto_start_request.auto_start_request", max_retries=2
)
def auto_start_request(self, request_id: str) -> str:
    if os.getenv("TESTING") == "1":
        return "skipped-test"

    async def _main() -> str:
        async with SessionLocal() as db:
            request = await db.get(Request, request_id)
            if request is None or not request.auto_start or not request.auto_agent_id:
                return "skipped"
            if await dependency_service.is_blocked(db, request_id):
                # 解锁之后又被加了新前置——不启动，等下一次解锁
                return "blocked"
            try:
                parent_run_id, runs, strategy = await assignment_service.assign(
                    db,
                    request_id=request_id,
                    user_id=request.owner_id,
                    assignee_type="agent",
                    assignee_id=request.auto_agent_id,
                    strategy=None,
                )
            except AppError as exc:
                # 配额超限、状态冲突、目标不可用：都不该让任务重试，
                # 记一条日志就够（需求保持 pending，用户可手动启动）
                await db.rollback()
                logger.warning(
                    "auto_start_rejected",
                    request_id=request_id,
                    code=exc.code,
                    message=exc.message,
                )
                return f"rejected:{exc.code}"
            await db.commit()
            # 入队必须在 commit 之后
            await assignment_service.dispatch_assigned(runs, strategy=strategy)
            logger.info(
                "auto_started",
                request_id=request_id,
                parent_run_id=parent_run_id,
                runs=len(runs),
            )
            return "queued"

    return asyncio.run(_main())
