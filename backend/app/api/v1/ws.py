"""运行实时事件 WebSocket 端点（T071，US5 + 007）：/ws/runs/{run_id} 与 /ws/requests/{request_id}。

对齐 contracts/api.md WebSocket 契约（FR-015，SC-003）：
- 握手凭证**优先取子协议**：`Sec-WebSocket-Protocol: aw.bearer, <access>`
  （浏览器 `new WebSocket(url, ['aw.bearer', token])`，服务端回显 `aw.bearer`）
- 兼容旧客户端的 `?token=<access>`：浏览器无法自定义握手头，早期实现把 token
  放进 URL；但 query 会进 nginx/SDK 的访问与错误日志，因此新客户端不再使用它
- **归属校验**：仅 Run 的 owner（或 admin）可订阅，否则 1008 forbidden
  （此前只验 token 不验归属，任意登录用户拿到 run_id 即可读取他人
   执行日志、步骤输入输出与最终结果——IDOR 越权）
- 订阅 Redis 频道 run:{run_id}，逐条转发事件到客户端
- 客户端 {"type": "ping"} → 服务端 {"type": "pong"}（保活）
- 连接断开清理订阅

007 新增 /ws/requests/{request_id}：分派类状态变更（pending/accepted/created）
发生在 Run 存在之前，塞不进 run 频道，故开 request:{id} 频道。
归属校验同构：owner、**当前 assignee** 或 admin 可订阅。

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

from app.core import metrics
from app.core.config import settings
from app.core.events import (
    subscribe_request_events,
    subscribe_run_events,
    subscribe_user_notifications,
)
from app.core.security import decode_token
from app.models.request import Request
from app.models.run import Run
from app.models.user import User
from app.services import request_service

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["ws"])

# 关闭码：1008 = policy violation（鉴权/权限语义，客户端不应无限重连）
_CLOSE_UNAUTHORIZED = (1008, "unauthorized")
_CLOSE_FORBIDDEN = (1008, "forbidden")

# 子协议名：客户端把凭证作为第二个子协议传入，服务端只回显这一个名字，
# 因此 token 不会出现在任何 URL/日志里。
_BEARER_SUBPROTOCOL = "aw.bearer"


def _credential(websocket: WebSocket) -> tuple[str | None, str | None]:
    """从握手取 access token，返回 (token, 需要回显的子协议)。

    优先 `Sec-WebSocket-Protocol: aw.bearer, <jwt>`；没有时回退到 `?token=`。
    """
    raw = websocket.headers.get("sec-websocket-protocol")
    if raw:
        parts = [part.strip() for part in raw.split(",")]
        if len(parts) >= 2 and parts[0] == _BEARER_SUBPROTOCOL:
            return parts[1], _BEARER_SUBPROTOCOL
    return websocket.query_params.get("token"), None


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
    """Run 归属校验：owner（需求创建者）或**需求当前接收人**或 admin 可订阅。

    007：分派后执行人 ≠ Run.owner_id（成本恒记 owner，research R-03），
    经手人必须能订阅执行事件，否则「实时可见」（US3）对分派链路失效。

    R1：owner-or-assignee 谓词走 `request_service.is_participant`。
    admin 放行是**显式叠加**的既有产品决策（016 spec：admin 读他人资源
    是否放行由各端点既有策略决定，本特性不改），刻意不并入通用谓词——
    否则「管理员能看所有执行日志」这条规则会变成隐式的、到处生效的默认。

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
            request = await db.get(Request, run.request_id)
            if request is not None and request_service.is_participant(request, user_id):
                return True
            user = await db.get(User, user_id)
            return user is not None and user.role == "admin"
    finally:
        await engine.dispose()


async def _can_access_request(request_id: str, user_id: str) -> bool:
    """需求 WS 归属校验（007）：owner、当前 assignee 或 admin 可订阅。

    与 _can_access_run 同构（一次性 NullPool engine + finally dispose）——
    理由见那里的注释：WS 端点可能跑在任意事件循环，复用全局连接池会死锁。
    """
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as db:
            request = await db.get(Request, request_id)
            if request is None:
                return False
            if request_service.is_participant(request, user_id):
                return True
            user = await db.get(User, user_id)
            return user is not None and user.role == "admin"
    finally:
        await engine.dispose()


@router.websocket("/ws/requests/{request_id}")
async def ws_request_events(websocket: WebSocket, request_id: str) -> None:
    """订阅 request:{request_id} 频道并转发分派类事件（007）。

    - 鉴权失败 → close(code=1008, reason="unauthorized")
    - 越权（非 owner/当前 assignee 且非 admin）→ close(code=1008, reason="forbidden")
    - 收到 {"type": "ping"} → 回 {"type": "pong"}
    """
    token, subprotocol = _credential(websocket)
    user_id = _authorize(token)
    if user_id is None:
        await websocket.close(*_CLOSE_UNAUTHORIZED)
        return

    if not await _can_access_request(request_id, user_id):
        logger.warning("ws_forbidden", request_id=request_id[:8], user_id=user_id[:8])
        await websocket.close(*_CLOSE_FORBIDDEN)
        return

    await websocket.accept(subprotocol=subprotocol)
    metrics.WS_CONNECTIONS.inc()

    send_lock = asyncio.Lock()

    async def send(payload: dict[str, Any]) -> None:
        async with send_lock:
            await websocket.send_json(payload)

    ready = asyncio.Event()

    async def _subscribe() -> None:
        await subscribe_request_events(request_id, send, on_ready=_mark_ready)

    async def _mark_ready() -> None:
        ready.set()

    sub_task = asyncio.create_task(_subscribe())

    try:
        await asyncio.wait_for(ready.wait(), timeout=5)
        await send({"type": "ready"})
    except TimeoutError:
        logger.warning("ws_subscribe_slow", request_id=request_id[:8])

    try:
        while True:
            message = await websocket.receive_json()
            if message.get("type") == "ping":
                await send({"type": "pong"})
    except WebSocketDisconnect:
        pass
    finally:
        metrics.WS_CONNECTIONS.dec()
        sub_task.cancel()
        try:
            await sub_task
        except (asyncio.CancelledError, Exception):  # noqa: S110, BLE001 - 任务取消/清理，静默即可
            pass


@router.websocket("/ws/runs/{run_id}")
async def ws_run_events(websocket: WebSocket, run_id: str) -> None:
    """订阅 run:{run_id} 频道并转发事件；ping → pong 保活。

    - 鉴权失败 → close(code=1008, reason="unauthorized")
    - 越权（非 owner 且非 admin）→ close(code=1008, reason="forbidden")
    - 收到 {"type": "ping"} → 回 {"type": "pong"}
    - 断开时清理订阅（subscribe_run_events 的 finally 负责）
    """
    token, subprotocol = _credential(websocket)
    user_id = _authorize(token)
    if user_id is None:
        await websocket.close(*_CLOSE_UNAUTHORIZED)
        return

    if not await _can_access_run(run_id, user_id):
        logger.warning("ws_forbidden", run_id=run_id[:8], user_id=user_id[:8])
        await websocket.close(*_CLOSE_FORBIDDEN)
        return

    # 只回显自己认识的那个子协议名，绝不把 token 回显给客户端
    await websocket.accept(subprotocol=subprotocol)
    metrics.WS_CONNECTIONS.inc()

    # 发送串行化：转发事件与 ready/pong 可能并发（同一 WebSocket 不能并发 send）
    send_lock = asyncio.Lock()

    async def send(payload: dict[str, Any]) -> None:
        async with send_lock:
            await websocket.send_json(payload)

    ready = asyncio.Event()

    async def _subscribe() -> None:
        """在独立任务中订阅（Redis pubsub listen 是阻塞式长循环）。"""
        await subscribe_run_events(run_id, send, on_ready=_mark_ready)

    async def _mark_ready() -> None:
        ready.set()

    sub_task = asyncio.create_task(_subscribe())

    # 订阅真正生效后才通知客户端：此前的发布不会丢，事件流可信
    try:
        await asyncio.wait_for(ready.wait(), timeout=5)
        await send({"type": "ready"})
    except TimeoutError:  # 3.11+ asyncio.TimeoutError 即内置 TimeoutError
        logger.warning("ws_subscribe_slow", run_id=run_id[:8])

    try:
        while True:
            message = await websocket.receive_json()
            if message.get("type") == "ping":
                await send({"type": "pong"})
            # 其他客户端消息暂不处理（预留：用户回复等）
    except WebSocketDisconnect:
        pass
    finally:
        metrics.WS_CONNECTIONS.dec()
        sub_task.cancel()
        try:
            await sub_task
        except (asyncio.CancelledError, Exception):  # noqa: S110, BLE001 - 任务取消/清理，静默即可
            pass


@router.websocket("/ws/notifications")
async def ws_notifications(websocket: WebSocket) -> None:
    """订阅 user:{id}:notifications 频道，实时下发站内通知（015）。

    为什么需要它：铃铛此前只有 30s 轮询（SC-007 的兜底口径）。分派/依赖解锁/
    成员变动这些事件本身是「有人在等我」的信号，晚半分钟就失去意义。

    鉴权：频道名由 token 里的 user_id 推导，**不接受客户端传入用户 id**——
    否则改一个参数就能订阅别人的通知。归属校验因此退化成「token 有效即为本人」。

    轮询仍然保留：WS 断线（网络抖动、代理超时）期间铃铛不能彻底不更新。
    """
    token, subprotocol = _credential(websocket)
    user_id = _authorize(token)
    if user_id is None:
        await websocket.close(*_CLOSE_UNAUTHORIZED)
        return

    await websocket.accept(subprotocol=subprotocol)
    metrics.WS_CONNECTIONS.inc()

    send_lock = asyncio.Lock()

    async def send(payload: dict[str, Any]) -> None:
        async with send_lock:
            await websocket.send_json(payload)

    ready = asyncio.Event()

    async def _mark_ready() -> None:
        ready.set()

    async def _subscribe() -> None:
        await subscribe_user_notifications(user_id, send, on_ready=_mark_ready)

    sub_task = asyncio.create_task(_subscribe())

    try:
        await asyncio.wait_for(ready.wait(), timeout=5)
        await send({"type": "ready"})
    except TimeoutError:
        logger.warning(
            "ws_subscribe_slow", channel="notifications", user_id=user_id[:8]
        )

    try:
        while True:
            message = await websocket.receive_json()
            if message.get("type") == "ping":
                await send({"type": "pong"})
    except WebSocketDisconnect:
        pass
    finally:
        metrics.WS_CONNECTIONS.dec()
        sub_task.cancel()
        try:
            await sub_task
        except (asyncio.CancelledError, Exception):  # noqa: S110, BLE001 - 任务取消/清理，静默即可
            pass
