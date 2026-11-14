"""运行实时事件 WebSocket 端点（T071，US5）：/ws/runs/{run_id}。

对齐 contracts/api.md WebSocket 契约（FR-015，SC-003）：
- 握手需 ?token=<access>；无效/缺失 token 拒绝（401）
- 订阅 Redis 频道 run:{run_id}，逐条转发事件到客户端
- 客户端 {"type": "ping"} → 服务端 {"type": "pong"}（保活）
- 连接断开清理订阅

用 Starlette WebSocket（FastAPI 原生），不依赖 httpx client——
WS 测试用同步 TestClient（starlette），走真实 Redis 发布/订阅。
"""

import asyncio
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from jose import JWTError

from app.core.events import channel_for_run, subscribe_run_events
from app.core.security import decode_token

router = APIRouter(tags=["ws"])


def _authorize(token: str | None) -> str | None:
    """校验 access token，返回 user_id；无效返回 None。"""
    if not token:
        return None
    try:
        payload = decode_token(token)
    except JWTError:
        return None
    if payload.get("type") != "access":
        return None
    return payload.get("user_id")


@router.websocket("/ws/runs/{run_id}")
async def ws_run_events(websocket: WebSocket, run_id: str) -> None:
    """订阅 run:{run_id} 频道并转发事件；ping → pong 保活。

    - 鉴权失败 → close(code=1008, reason="unauthorized")
    - 收到 {"type": "ping"} → 回 {"type": "pong"}
    - 断开时清理订阅（subscribe_run_events 的 finally 负责）
    """
    token = websocket.query_params.get("token")
    user_id = _authorize(token)
    if user_id is None:
        await websocket.close(code=1008, reason="unauthorized")
        return

    await websocket.accept()

    stop = asyncio.Event()
    channel = channel_for_run(run_id)

    async def forward(payload: dict[str, Any]) -> None:
        await websocket.send_json(payload)

    async def _subscribe() -> None:
        """在独立任务中订阅（Redis pubsub listen 是阻塞式长循环）。"""
        await subscribe_run_events(run_id, forward)

    sub_task = asyncio.create_task(_subscribe())

    try:
        while True:
            message = await websocket.receive_json()
            if message.get("type") == "ping":
                await websocket.send_json({"type": "pong"})
            # 其他客户端消息暂不处理（预留：用户回复等）
    except WebSocketDisconnect:
        pass
    finally:
        stop.set()
        sub_task.cancel()
        try:
            await sub_task
        except (asyncio.CancelledError, Exception):
            pass
