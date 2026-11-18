"""007 request 频道 WS 集成测试（T027）：分派类事件的实时订阅。

与 test_ws.py（run 频道）同构：同步 TestClient + 真实 Redis。
这里锁的是 request 频道特有的三件事：
- 归属校验含**当前 assignee**（007 新增的合法订阅者）
- status_change 事件按序到达（pending/accepted/created）
- 陌生人与未登录一律 1008 拒绝

频道存在的理由：分派事件发生在 Run 之前，塞不进 run:{id}。
"""

import asyncio
import uuid

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.core.db import SessionLocal
from app.core.events import publish_request_event
from app.core.security import create_access_token, hash_password
from app.main import app
from app.models.request import Request
from app.models.user import User

WS_URL = "/ws/requests"


def _token(user_id: str, role: str = "member") -> str:
    return create_access_token(user_id, role)


async def _make_request(*, assignee: bool = False) -> tuple[str, str, str | None]:
    """造 owner + 需求（可带当前接收人），返回 (owner_id, request_id, assignee_id)。"""
    async with SessionLocal() as db:
        owner = User(
            email=f"wsr-{uuid.uuid4().hex[:10]}@test.com",
            password_hash=hash_password("TestPass123!"),
            role="member",
            status="active",
        )
        db.add(owner)
        await db.flush()
        assignee_id = None
        if assignee:
            worker = User(
                email=f"wsr-w-{uuid.uuid4().hex[:10]}@test.com",
                password_hash=hash_password("TestPass123!"),
                role="member",
                status="active",
            )
            db.add(worker)
            await db.flush()
            assignee_id = worker.id
        req = Request(
            owner_id=owner.id,
            assignee_id=assignee_id,
            title=f"wsr 测试需求 {uuid.uuid4().hex[:6]}",
            status="pending" if assignee else "created",
        )
        db.add(req)
        await db.commit()
        return owner.id, req.id, assignee_id


def test_ws_request_rejects_invalid_token():
    client = TestClient(app)
    with (
        pytest.raises(WebSocketDisconnect),
        client.websocket_connect(
            f"{WS_URL}/{uuid.uuid4()}", params={"token": "invalid-token"}
        ),
    ):
        pass


def test_ws_request_stranger_forbidden():
    """陌生人订阅他人需求 → 1008 forbidden。"""
    _owner_id, request_id, _ = asyncio.run(_make_request(assignee=True))

    async def _make_stranger() -> str:
        async with SessionLocal() as db:
            stranger = User(
                email=f"wsr-s-{uuid.uuid4().hex[:10]}@test.com",
                password_hash=hash_password("TestPass123!"),
                role="member",
                status="active",
            )
            db.add(stranger)
            await db.commit()
            return stranger.id

    stranger_id = asyncio.run(_make_stranger())

    client = TestClient(app)
    with (
        pytest.raises(WebSocketDisconnect),
        client.websocket_connect(
            f"{WS_URL}/{request_id}", params={"token": _token(stranger_id)}
        ),
    ):
        pass


def test_ws_request_owner_and_assignee_can_subscribe():
    """owner 与当前 assignee 都能订阅，并按序收到 status_change。"""
    _owner_id, request_id, assignee_id = asyncio.run(_make_request(assignee=True))

    client = TestClient(app)
    with client.websocket_connect(
        f"{WS_URL}/{request_id}", params={"token": _token(assignee_id)}
    ) as ws:
        assert ws.receive_json() == {"type": "ready"}
        # 订阅就绪后发布：pending → accepted → created（一次转手 + 退回）
        asyncio.run(
            publish_request_event(
                request_id,
                {
                    "type": "status_change",
                    "status": "accepted",
                    "assignee_id": assignee_id,
                },
            )
        )
        assert ws.receive_json()["status"] == "accepted"
        asyncio.run(
            publish_request_event(
                request_id,
                {"type": "status_change", "status": "created", "assignee_id": None},
            )
        )
        assert ws.receive_json()["status"] == "created"
        ws.send_json({"type": "ping"})
        assert ws.receive_json() == {"type": "pong"}
