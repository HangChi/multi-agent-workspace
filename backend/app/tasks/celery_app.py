"""Celery 应用：broker=Redis，JSON 序列化，结果后端。"""

from celery import Celery
from celery.schedules import crontab

from app.core.config import settings

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
    timezone="Asia/Shanghai",
)

# 周期任务（Beat）：僵尸 Run 回收，避免 worker 崩溃后看板永久卡「执行中」
celery_app.conf.beat_schedule = {
    "reap-stale-runs": {
        "task": "app.tasks.maintenance.reap_stale_runs",
        "schedule": crontab(minute="*/5"),
    },
}

# 自动发现 app/tasks 下的任务模块。
# 注意：autodiscover_tasks 只会导入 packages 参数本身（related_name="tasks"），
# 不会递归扫描子模块，因此必须在 __init__.py 为空时显式导入任务模块。
celery_app.autodiscover_tasks(["app.tasks"])

# 显式导入任务模块，确保 Worker 注册（兜底 autodiscover 不递归的局限）
from app.tasks import execute_run, maintenance  # noqa: F401  (must be after celery_app is defined)
