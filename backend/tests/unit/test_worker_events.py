"""Worker 事件发布：同步单例客户端 + Redis 抖动不打断执行。

回归点（2026-11-17 审计）：
- 原实现每个事件 `asyncio.run(...)`，等于每发一条事件新建一个事件循环 + 一条
  TCP 连接；沙箱 stdout 按 4 KiB 分块，1 MB 输出就是 256 次建连。
- 原实现只捕 `ConnectionError/OSError`，而 `redis.exceptions.ConnectionError`
  两个都不继承，Redis 抖动会直接把 Worker 的整次执行打断。
"""

from __future__ import annotations

import redis

from app.tasks import events


class _FakeRedis:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.published: list[tuple[str, str]] = []

    def publish(self, channel: str, payload: str) -> int:
        if self.fail:
            raise redis.exceptions.ConnectionError("redis down")
        self.published.append((channel, payload))
        return 1


def test_client_is_process_wide_singleton(monkeypatch):
    """同一进程内复用一条连接池，不再每次 publish 都 from_url。"""
    created: list[object] = []

    def fake_from_url(*args, **kwargs):
        client = _FakeRedis()
        created.append(client)
        return client

    monkeypatch.setattr(events, "_client", None)
    monkeypatch.setattr(events.redis, "from_url", fake_from_url)

    first = events._redis_client()
    second = events._redis_client()

    assert first is second
    assert len(created) == 1

    events._client = None  # 不污染同进程内的其他测试


def test_client_has_connect_and_socket_timeouts(monkeypatch):
    """Redis 抖动时必须快速失败，不能把 Worker 挂在 publish 上。"""
    captured: dict[str, object] = {}

    def fake_from_url(url, **kwargs):
        captured.update(kwargs)
        return _FakeRedis()

    monkeypatch.setattr(events, "_client", None)
    monkeypatch.setattr(events.redis, "from_url", fake_from_url)

    events._redis_client()

    assert captured["socket_timeout"] == 2.0
    assert captured["socket_connect_timeout"] == 2.0

    events._client = None


def test_publish_uses_run_channel_and_json(monkeypatch):
    client = _FakeRedis()
    monkeypatch.setattr(events, "_client", client)

    events.status_change("run-1", "running")

    channel, payload = client.published[0]
    assert channel == "run:run-1"
    assert '"status_change"' in payload
    assert '"running"' in payload


def test_redis_outage_does_not_escape(monkeypatch):
    """Redis 挂了只记 warning，不能让执行链路抛错。"""
    monkeypatch.setattr(events, "_client", _FakeRedis(fail=True))

    events.run_failed("run-1", "boom")  # 不抛异常即通过
