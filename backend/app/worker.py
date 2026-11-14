"""Worker 入口：`celery -A app.worker worker --loglevel=info` 启动。"""

from app.tasks.celery_app import celery_app

__all__ = ["celery_app"]
