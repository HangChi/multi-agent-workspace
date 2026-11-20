"""Celery 应用：broker=Redis，JSON 序列化，结果后端。"""

import redis
from celery import Celery
from celery.schedules import crontab
from celery.signals import worker_process_init

from app.core.config import settings
from app.core.log_setup import configure_logging

# Worker/Beat 进程不加载 main.py，需自行初始化 structlog（脱敏 + JSON + 上下文）
configure_logging()

celery_app = Celery(
    "agent-workbench",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_track_started=True,
    task_acks_late=True,
    # Worker 被杀时把任务退回队列（配合 acks_late；execute_run 有 Redis 锁防重复执行）
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    # 任务硬上限：Agent 的 max_run_seconds 默认 1800，这里留一倍余量。
    # 没有它时，任何卡死的任务（如 LLM 挂起）会永久占住一个 Worker 槽位。
    # 软上限先抛 SoftTimeLimitExceeded 给任务一次收尾机会，硬上限强杀；
    # task_reject_on_worker_lost + 执行锁保证杀掉的 Run 不会被重复消费。
    task_soft_time_limit=3300,
    task_time_limit=3600,
    # Redis 作为 broker 时，消息可见性超时必须 ≥ 最长任务时长，
    # 否则长任务会被判定「未被确认」并重新投递（默认 1h，与硬上限对齐）。
    broker_transport_options={"visibility_timeout": 3600},
    timezone="Asia/Shanghai",
)

# 周期任务（Beat）：回收僵尸 Run，并每日清除超过 90 天的沙箱脚本和输出。
celery_app.conf.beat_schedule = {
    "reap-stale-runs": {
        "task": "app.tasks.maintenance.reap_stale_runs",
        "schedule": crontab(minute="*/5"),
    },
    "purge-expired-sandbox-data": {
        "task": "app.tasks.maintenance.purge_expired_sandbox_data",
        "schedule": crontab(hour=3, minute=0),
    },
    # 定时调度扫描（004）：每分钟一次。宽限期 90s 恰好覆盖这一周期，
    # 单次抖动不会被误记成"错过"。
    "scan-due-schedules": {
        "task": "app.tasks.schedule.scan_due_schedules",
        "schedule": crontab(minute="*"),
    },
    # 调度触发历史清理（004）：与沙箱清理同批，每天 03:00 后各跑各的。
    "prune-schedule-history": {
        "task": "app.tasks.maintenance.prune_schedule_history",
        "schedule": crontab(hour=3, minute=30),
    },
}


# 自动发现 app/tasks 下的任务模块。
# 注意：autodiscover_tasks 只会导入 packages 参数本身（related_name="tasks"），
# 不会递归扫描子模块，因此必须在 __init__.py 为空时显式导入任务模块。
@worker_process_init.connect
def _init_sentry_in_worker(**_kwargs: object) -> None:
    """每个 worker 子进程单独初始化 Sentry（DSN 为空时是空操作）。

    不能只在外层进程 init：prefork 模式下 SDK 的后台传输线程与网络连接
    不会随 fork 复制，子进程里抛的异常会静默丢掉。Sentry 官方对 celery
    的要求也是挂在 worker_process_init 上。
    """
    from app.core.sentry import init_sentry

    init_sentry()


@worker_process_init.connect
def _start_metrics_publisher(**_kwargs: object) -> None:
    """每个 worker 子进程启动时，把本进程指标周期性发布到 Redis。

    为什么需要：LLM 调用数、沙箱执行数、Run 终态数都发生在 Worker 进程里，
    而 `/metrics` 由 API 提供。不发布的话这些指标在抓取端永远是 0，
    对应的告警也就永远不会响。
    """
    from app.core import metrics
    from app.core.config import settings

    def client_factory() -> redis.Redis:
        return redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_timeout=2.0,
            socket_connect_timeout=2.0,
        )

    metrics.start_background_publisher(client_factory)


celery_app.autodiscover_tasks(["app.tasks"])

# 显式导入任务模块，确保 Worker 注册（兜底 autodiscover 不递归的局限）
from app.tasks import (  # noqa: F401  (must be after celery_app is defined)
    execute_run,
    maintenance,
    schedule,
    validate_external_server,
)
