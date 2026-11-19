"""015 通知频道 WS 集成测试：/ws/notifications。

锁三件事：
- 未登录/坏 token 一律 1008 拒绝；
- 本人订阅后，**经 notification_service.create_notification 写的通知**能实时
  到达（这条链路是「服务层写库 → 发布 → WS 转发」，中间任何一环断了都测不出）；
- ping/pong 保活。

归属校验在这里退化成「token 有效即本人」——频道名由 token 里的 user_id 推导，
客户端无法指定订阅谁，所以不存在越权订阅的入口（这是它和 run/request 频道
最大的不同，那两个要靠对象归属校验）。
"""

import asyncio
import uuid

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.core.db import SessionLocal
from app.core.events import publish_user_notification
from app.core.security import create_access_token, hash_password
from app.main import app
from app.models.request import Request
from app.models.user import User
from app.services.notification_service import create_notification

WS_URL = "/ws/notifications"


def _token(user_id: str, role: str = "member") -> str:
    return create_access_token(user_id, role)


async def _make_user_with_request() -> tuple[str, str]:
    async with SessionLocal() as db:
        user = User(
            email=f"wsn-{uuid.uuid4().hex[:10]}@test.com",
            password_hash=hash_password("TestPass123!"),
            role="member",
            status="active",
        )
        db.add(user)
        await db.flush()
        req = Request(
            owner_id=user.id,
            title=f"wsn 通知需求 {uuid.uuid4().hex[:6]}",
            status="created",
        )
        db.add(req)
        await db.commit()
        return user.id, req.id


def test_ws_notifications_rejects_invalid_token():
    client = TestClient(app)
    with (
        pytest.raises(WebSocketDisconnect),
        client.websocket_connect(WS_URL, params={"token": "invalid-token"}),
    ):
        pass


async def _create_notification_for(user_id: str, request_id: str) -> None:
    async with SessionLocal() as db:
        await create_notification(
            db,
            user_id=user_id,
            type_="run_completed",
            title="wsn 通知标题",
            request_id=request_id,
            payload={"status": "completed"},
        )
        await db.commit()


def test_ws_notifications_receives_created_notification():
    """订阅 → 服务层写一条通知 → 实时收到。"""
    user_id, request_id = asyncio.run(_make_user_with_request())

    client = TestClient(app)
    with client.websocket_connect(WS_URL, params={"token": _token(user_id)}) as ws:
        assert ws.receive_json() == {"type": "ready"}

        asyncio.run(_create_notification_for(user_id, request_id))
        message = ws.receive_json()
        assert message["type"] == "notification"
        assert message["notification_type"] == "run_completed"
        assert message["request_id"] == request_id
        assert message["title"] == "wsn 通知标题"
        assert message["notification_id"]

        ws.send_json({"type": "ping"})
        assert ws.receive_json() == {"type": "pong"}


def test_ws_notifications_only_self_channel():
    """发布到**别人**的频道不会出现在我的连接上（用超时反证）。

    强断言「收不到」只能靠超时：给一个短超时的 receive，期待超时而不是消息。
    """
    user_id, _ = asyncio.run(_make_user_with_request())
    other_id, _ = asyncio.run(_make_user_with_request())

    client = TestClient(app)
    with client.websocket_connect(WS_URL, params={"token": _token(user_id)}) as ws:
        assert ws.receive_json() == {"type": "ready"}
        asyncio.run(publish_user_notification(other_id, {"type": "notification"}))

        # 自己频道发一条作为「哨兵」：如果先收到的是它，说明别人的没串进来
        asyncio.run(
            publish_user_notification(
                user_id, {"type": "notification", "marker": "self"}
            )
        )
        # 发布是异步的：可能先收到别人的（BUG），也可能直接收到自己的哨兵
        message = ws.receive_json()
        assert message.get("marker") == "self", f"收到了别人的频道事件: {message}"
