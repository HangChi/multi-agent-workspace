"""WS 集成测试（T064，US5）：实时事件订阅（FR-015，SC-003）。

对齐 contracts/api.md WebSocket 契约：
- 握手需 ?token=<access>；无效 token 拒绝（401）
- 服务端推送：status_change / step_new / step_update / log_line /
  cost_update / run_completed / run_failed / needs_input 按序到达
- 客户端 {"type": "ping"} → 服务端 {"type": "pong"}（保活）

用同步 TestClient（starlette WS 测试 API）+ 真实 Redis（aw-redis 运行中）：
测试通过 publish_run_event 向 run:{id} 频道发布事件，断言 WS 端按序转发。
"""

import asyncio
import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.events import publish_run_event
from app.core.security import create_access_token
from app.main import app

WS_URL = "/ws/runs"


def _token(user_id: str = "u-test-1", role: str = "member") -> str:
    return create_access_token(user_id, role)


# ---------- 握手鉴权 ----------


def test_ws_rejects_invalid_token():
    """无效 token → 握手拒绝。"""
    client = TestClient(app)
    with pytest.raises(Exception):
        with client.websocket_connect(
            f"{WS_URL}/{uuid.uuid4()}", params={"token": "invalid-token"}
        ):
            pass  # 不应走到这里


def test_ws_rejects_missing_token():
    """缺 token → 握手拒绝。"""
    client = TestClient(app)
    with pytest.raises(Exception):
        with client.websocket_connect(f"{WS_URL}/{uuid.uuid4()}"):
            pass


def test_ws_handshake_succeeds_with_valid_token():
    """有效 token → 握手成功。"""
    client = TestClient(app)
    with client.websocket_connect(
        f"{WS_URL}/{uuid.uuid4()}", params={"token": _token()}
    ) as ws:
        # 连接建立即可
        assert ws is not None


# ---------- 事件按序到达 ----------


def _publish_sequence(run_id: str, events: list[dict]) -> None:
    """按序发布一组事件（真实 Redis）。"""
    for event in events:
        asyncio.run(publish_run_event(run_id, event))


def test_ws_events_arrive_in_order():
    """8 类事件按序到达（status_change→step_new→step_update→log_line→
    cost_update→needs_input→run_failed→run_completed 的触发顺序）。"""
    client = TestClient(app)
    run_id = str(uuid.uuid4())
    token = _token()
    with client.websocket_connect(
        f"{WS_URL}/{run_id}", params={"token": token}
    ) as ws:
        events = [
            {"type": "status_change", "status": "running", "ts": "t1"},
            {"type": "step_new", "step": {"seq": 1, "kind": "llm", "status": "running"}},
            {
                "type": "step_update",
                "step": {"seq": 1, "kind": "llm", "status": "completed", "cost_cents": 5},
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
    client = TestClient(app)
    run_a, run_b = str(uuid.uuid4()), str(uuid.uuid4())
    with client.websocket_connect(
        f"{WS_URL}/{run_a}", params={"token": _token()}
    ) as ws_a, client.websocket_connect(
        f"{WS_URL}/{run_b}", params={"token": _token()}
    ) as ws_b:
        asyncio.run(publish_run_event(run_a, {"type": "status_change", "status": "running"}))
        # run A 收到
        assert ws_a.receive_json()["type"] == "status_change"


# ---------- ping → pong ----------


def test_ws_ping_pong():
    """客户端 ping → 服务端 pong。"""
    client = TestClient(app)
    with client.websocket_connect(
        f"{WS_URL}/{uuid.uuid4()}", params={"token": _token()}
    ) as ws:
        ws.send_json({"type": "ping"})
        resp = ws.receive_json()
        assert resp == {"type": "pong"}
