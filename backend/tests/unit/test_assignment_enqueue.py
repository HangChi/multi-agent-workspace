"""分配后的副作用必须降级而不是把已提交的受理变成 500。

`assign()` 先提交事务再入队/发事件；broker 或 Redis 抖动时如果异常类型没被
捕获，客户端会收到 500，而库里 Run 与需求状态已经变更 —— 重试会撞 409，
Run 又永远不会被消费。这两条路径必须实测真实的第三方异常类型。
"""

from __future__ import annotations

import redis.exceptions
from kombu.exceptions import OperationalError as KombuOperationalError

from app.services import assignment_service


def test_enqueue_swallows_kombu_operational_error(monkeypatch):
    from app.tasks import celery_app as celery_module

    monkeypatch.delenv("TESTING", raising=False)
    sent: list[str] = []

    def send_task(name: str, args: list[str] | None = None, **_kwargs):
        sent.append(args[0] if args else name)
        raise KombuOperationalError("broker is down")

    monkeypatch.setattr(celery_module.celery_app, "send_task", send_task)

    assignment_service._enqueue(["run-1", "run-2"])

    # 每个 Run 都尝试过（一个失败不影响另一个），且没有任何异常逃逸
    assert sent == ["run-1", "run-2"]


async def test_publish_assigned_swallows_redis_connection_error(monkeypatch):
    monkeypatch.delenv("TESTING", raising=False)
    published: list[str] = []

    async def publish(run_id: str, _event: dict) -> None:
        published.append(run_id)
        raise redis.exceptions.ConnectionError("redis is down")

    monkeypatch.setattr(assignment_service, "publish_run_event", publish)

    await assignment_service._publish_assigned(["run-1"])

    assert published == ["run-1"]
