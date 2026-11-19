"""core.events 事件总线的降级与容错（015 覆盖率补齐）。

`publish_user_notification` 与 `_subscribe` 承载两条「坏了才暴露」的策略：

1. 通知已落库，推送通道抖动**不能**变成业务失败——只记日志不抛；否则一次
   Redis 抖动会让「分派/建项目/执行完成」这些本已成功的操作对客户端报错。
2. 订阅循环遇到坏 JSON / 非 message 消息时**跳过**而非退出——生产环境的脏
   数据不能让整个实时链路静默停摆。
"""

import json

from app.core import events


async def test_publish_user_notification_swallows_redis_failure(monkeypatch):
    """通知已落库，推送通道抖动不能变成业务失败——只记日志不抛。"""

    class _BoomClient:
        async def publish(self, *_a, **_k):
            raise ConnectionError("redis down")

    monkeypatch.setattr(events, "_redis_client", lambda: _BoomClient())
    # 不应抛异常
    await events.publish_user_notification("user-12345", {"type": "notification"})


async def test_publish_user_notification_publishes_json(monkeypatch):
    captured: dict = {}

    class _Client:
        async def publish(self, channel, message):
            captured["channel"] = channel
            captured["message"] = message

    monkeypatch.setattr(events, "_redis_client", lambda: _Client())
    await events.publish_user_notification(
        "user-abc", {"type": "notification", "title": "标题"}
    )

    assert captured["channel"] == "user:user-abc:notifications"
    # ensure_ascii=False：中文原样保留，前端不用二次解码
    assert "标题" in captured["message"]
    assert json.loads(captured["message"])["type"] == "notification"


async def test_subscribe_skips_invalid_json_and_non_message(monkeypatch):
    """坏 JSON 与非 message 消息跳过而非崩：脏数据不能让订阅循环退出。"""
    handled: list = []

    class _PubSub:
        def __init__(self):
            self.subscribed = None
            self.unsubscribed = None

        async def subscribe(self, channel):
            self.subscribed = channel

        async def unsubscribe(self, channel):
            self.unsubscribed = channel

        async def aclose(self):
            pass

        async def listen(self):
            yield {"type": "message", "data": "not-json"}
            yield {"type": "subscribe", "data": b""}
            yield {"type": "message", "data": json.dumps({"ok": True})}

    class _Client:
        def pubsub(self):
            return _PubSub()

    monkeypatch.setattr(events, "_redis_client", lambda: _Client())
    ready: list = []

    async def handler(payload):
        handled.append(payload)

    async def on_ready():
        ready.append(1)

    await events._subscribe("run:x", handler, on_ready=on_ready)

    assert handled == [{"ok": True}]
    assert ready == [1]
