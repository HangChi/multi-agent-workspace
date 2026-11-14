"""运行实时事件 WebSocket 端点（T071，US5）：/ws/runs/{run_id}。

对齐 contracts/api.md WebSocket 契约（FR-015，SC-003）：
- 握手需 ?token=<access>；无效/缺失 token 拒绝（1008 unauthorized）
- **归属校验**：仅 Run 的 owner（或 admin）可订阅，否则 1008 forbidden
  （此前只验 token 不验归属，任意登录用户拿到 run_id 即可读取他人
   执行日志、步骤输入输出与最终结果——IDOR 越权）
- 订阅 Redis 频道 run:{run_id}，逐条转发事件到客户端
- 客户端 {"type": "ping"} → 服务端 {"type": "pong"}（保活）
- 连接断开清理订阅

用 Starlette WebSocket（FastAPI 原生），不依赖 httpx client——
WS 测试用同步 TestClient（starlette），走真实 Redis 发布/订阅。
"""

import asyncio
from typing import Any

import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from jose import JWTError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.core.events import subscribe_run_events
from app.core.security import decode_token
from app.models.run import Run
from app.models.user import User

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["ws"])

# 关闭码：1008 = policy violation（鉴权/权限语义，客户端不应无限重连）
_CLOSE_UNAUTHORIZED = (1008, "unauthorized")
_CLOSE_FORBIDDEN = (1008, "forbidden")


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


async def _can_access_run(run_id: str, user_id: str) -> bool:
    """Run 归属校验：owner 或 admin 可订阅，其余拒绝。

    用一次性的 NullPool engine 并在 finally dispose：
    WS 端点可能运行在任意事件循环中（生产是 uvicorn 主 loop，测试是 TestClient
    自建 loop），复用全局连接池会让 asyncpg 连接跨 loop 复用而死锁。
    握手频率低（每连接一次），建连开销可忽略。
    """
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as db:
            run = await db.get(Run, run_id)
            if run is None:
                return False
            if run.owner_id == user_id:
                return True
            user = await db.get(User, user_id)
            return user is not None and user.role == "admin"
    finally:
        await engine.dispose()


@router.websocket("/ws/runs/{run_id}")
async def ws_run_events(websocket: WebSocket, run_id: str) -> None:
    """订阅 run:{run_id} 频道并转发事件；ping → pong 保活。

    - 鉴权失败 → close(code=1008, reason="unauthorized")
    - 越权（非 owner 且非 admin）→ close(code=1008, reason="forbidden")
    - 收到 {"type": "ping"} → 回 {"type": "pong"}
    - 断开时清理订阅（subscribe_run_events 的 finally 负责）
    """
    user_id = _authorize(websocket.query_params.get("token"))
    if user_id is None:
        await websocket.close(*_CLOSE_UNAUTHORIZED)
        return

    if not await _can_access_run(run_id, user_id):
        logger.warning("ws_forbidden", run_id=run_id[:8], user_id=user_id[:8])
        await websocket.close(*_CLOSE_FORBIDDEN)
        return

    await websocket.accept()

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
        sub_task.cancel()
        try:
            await sub_task
        except (asyncio.CancelledError, Exception):  # noqa: S110, BLE001 - 任务取消/清理，静默即可
            pass
