"""WS 集成测试（T064，US5）：实时事件订阅（FR-015，SC-003）。

对齐 contracts/api.md WebSocket 契约：
- 握手需 ?token=<access>；无效 token 拒绝（1008 unauthorized）
- **归属校验**：非 owner（且非 admin）订阅 → 1008 forbidden（IDOR 防护）
- 服务端推送：status_change / step_new / step_update / log_line /
  cost_update / run_completed / run_failed / needs_input 按序到达
- 客户端 {"type": "ping"} → 服务端 {"type": "pong"}（保活）

用同步 TestClient（starlette WS 测试 API）+ 真实 Redis（aw-redis 运行中）：
测试用 `_make_run()` 造真实 User/Request/Run（归属校验需要），
再通过 publish_run_event 向 run:{id} 频道发布事件，断言 WS 端按序转发。
"""

import asyncio
import uuid

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.core.db import SessionLocal
from app.core.events import publish_run_event
from app.core.security import create_access_token, hash_password
from app.main import app
from app.models.request import Request
from app.models.run import Run
from app.models.user import User

WS_URL = "/ws/runs"


def _token(user_id: str, role: str = "member") -> str:
    return create_access_token(user_id, role)


async def _make_run(*, role: str = "member") -> tuple[str, str]:
    """造真实 User + Request + Run，返回 (user_id, run_id)。"""
    async with SessionLocal() as db:
        user = User(
            email=f"ws-{uuid.uuid4().hex[:10]}@test.com",
            password_hash=hash_password("TestPass123!"),
            role=role,
            status="active",
        )
        db.add(user)
        await db.flush()

        req = Request(
            owner_id=user.id,
            title=f"ws 测试需求 {uuid.uuid4().hex[:6]}",
            description="ws 事件订阅",
            priority="normal",
            status="running",
        )
        db.add(req)
        await db.flush()

        run = Run(
            request_id=req.id,
            owner_id=user.id,
            assignee_type="agent",
            assignee_id=str(uuid.uuid4()),
            strategy="direct",
            status="queued",
        )
        db.add(run)
        await db.commit()
        return user.id, run.id


# ---------- 握手鉴权 ----------


def test_ws_rejects_invalid_token():
    """无效 token → 握手拒绝。"""
    client = TestClient(app)
    with (
        pytest.raises(WebSocketDisconnect),
        client.websocket_connect(
            f"{WS_URL}/{uuid.uuid4()}", params={"token": "invalid-token"}
        ),
    ):
        pass  # 不应走到这里


def test_ws_rejects_missing_token():
    """缺 token → 握手拒绝。"""
    client = TestClient(app)
    with (
        pytest.raises(WebSocketDisconnect),
        client.websocket_connect(f"{WS_URL}/{uuid.uuid4()}"),
    ):
        pass


def test_ws_handshake_succeeds_for_owner():
    """有效 token + owner → 握手成功。"""
    user_id, run_id = asyncio.run(_make_run())
    client = TestClient(app)
    with client.websocket_connect(
        f"{WS_URL}/{run_id}", params={"token": _token(user_id)}
    ) as ws:
        # 订阅就绪后才发 ready：此后发布的事件不会丢
        assert ws.receive_json() == {"type": "ready"}


# ---------- 归属校验（IDOR 防护）----------


def test_ws_rejects_non_owner():
    """他人 Run：非 owner 订阅 → 1008 forbidden，不能读日志/结果。"""
    _owner_id, run_id = asyncio.run(_make_run())
    intruder_id, _ = asyncio.run(_make_run())

    client = TestClient(app)
    with (
        pytest.raises(WebSocketDisconnect) as exc,
        client.websocket_connect(
            f"{WS_URL}/{run_id}", params={"token": _token(intruder_id)}
        ),
    ):
        pass
    # 1008 = policy violation（鉴权/权限语义）
    assert exc.value.code == 1008


def test_ws_rejects_unknown_run():
    """不存在的 run_id → 拒绝（不泄漏是否存在）。"""
    user_id, _ = asyncio.run(_make_run())
    client = TestClient(app)
    with (
        pytest.raises(WebSocketDisconnect),
        client.websocket_connect(
            f"{WS_URL}/{uuid.uuid4()}", params={"token": _token(user_id)}
        ),
    ):
        pass


def test_ws_admin_can_subscribe_any_run():
    """admin 可订阅任意 Run（运维排查需要）。"""
    _owner_id, run_id = asyncio.run(_make_run())
    admin_id, _ = asyncio.run(_make_run(role="admin"))

    client = TestClient(app)
    with client.websocket_connect(
        f"{WS_URL}/{run_id}", params={"token": _token(admin_id, role="admin")}
    ) as ws:
        assert ws.receive_json() == {"type": "ready"}


# ---------- 事件按序到达 ----------


def _publish_sequence(run_id: str, events: list[dict]) -> None:
    """按序发布一组事件（真实 Redis）。"""
    for event in events:
        asyncio.run(publish_run_event(run_id, event))


def test_ws_events_arrive_in_order():
    """8 类事件按序到达（status_change→step_new→step_update→log_line→
    cost_update→needs_input→run_failed→run_completed 的触发顺序）。"""
    user_id, run_id = asyncio.run(_make_run())
    client = TestClient(app)
    with client.websocket_connect(
        f"{WS_URL}/{run_id}", params={"token": _token(user_id)}
    ) as ws:
        assert ws.receive_json() == {"type": "ready"}
        events = [
            {"type": "status_change", "status": "running", "ts": "t1"},
            {
                "type": "step_new",
                "step": {"seq": 1, "kind": "llm", "status": "running"},
            },
            {
                "type": "step_update",
                "step": {
                    "seq": 1,
                    "kind": "llm",
                    "status": "completed",
                    "cost_cents": 5,
                },
            },
            {"type": "log_line", "line": "正在调用工具 web_search...", "level": "info"},
            {"type": "cost_update", "cost_cents": 5},
            {"type": "needs_input", "message": "需要补充信息: 请提供目标"},
            {"type": "run_failed", "error": "LLM 调用失败"},
            {"type": "run_completed", "result": {"answer": "ok"}},
        ]
        _publish_sequence(run_id, events)

        received = [ws.receive_json() for _ in events]
        assert [r["type"] for r in received] == [e["type"] for e in events]
        # 事件体原样透传
        assert received[0]["status"] == "running"
        assert received[3]["line"].startswith("正在调用")
        assert received[6]["error"] == "LLM 调用失败"
        assert received[7]["result"] == {"answer": "ok"}


def test_ws_events_isolated_per_run():
    """事件按 run 隔离：run A 的事件不会到达 run B 的连接。"""
    user_id, run_a = asyncio.run(_make_run())
    _same_owner, run_b = asyncio.run(_make_run())
    # 让 run_b 同属该用户，验证「同一 owner 的不同 run」也按频道隔离
    asyncio.run(_reassign_owner(run_b, user_id))

    client = TestClient(app)
    token = _token(user_id)
    with (
        client.websocket_connect(f"{WS_URL}/{run_a}", params={"token": token}) as ws_a,
        client.websocket_connect(f"{WS_URL}/{run_b}", params={"token": token}),
    ):
        assert ws_a.receive_json() == {"type": "ready"}
        asyncio.run(
            publish_run_event(run_a, {"type": "status_change", "status": "running"})
        )
        # run A 收到
        assert ws_a.receive_json()["type"] == "status_change"


async def _reassign_owner(run_id: str, owner_id: str) -> None:
    """把 run 的 owner 改成指定用户（构造「同一用户多个 run」场景）。"""
    async with SessionLocal() as db:
        run = await db.get(Run, run_id)
        assert run is not None
        run.owner_id = owner_id
        await db.commit()


# ---------- ping → pong ----------


def test_ws_ping_pong():
    """客户端 ping → 服务端 pong。"""
    user_id, run_id = asyncio.run(_make_run())
    client = TestClient(app)
    with client.websocket_connect(
        f"{WS_URL}/{run_id}", params={"token": _token(user_id)}
    ) as ws:
        assert ws.receive_json() == {"type": "ready"}
        ws.send_json({"type": "ping"})
        resp = ws.receive_json()
        assert resp == {"type": "pong"}
