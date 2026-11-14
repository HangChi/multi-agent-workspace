"""请求上下文中间件：X-Request-ID 串联 + 结构化访问日志。

作用：
1. 每个请求分配/透传 request_id，绑进 structlog contextvars——
   该请求内所有日志（含 service/worker 传递的）自动带同一个 id，
   排查时能按一次请求聚合，而不用靠时间戳猜。
2. 记录方法/路径/状态码/耗时/客户端 IP（从 nginx 的 X-Real-IP 取）。
3. 响应头回传 X-Request-ID，前端报错时可直接带上来。
4. 健康检查与静态资源降噪（不记录，避免刷屏）。

注意：不读取请求体（会消费流影响下游）；不记录 query string 原值，
只记录 path（避免 token 通过 query 落到日志——WS 握手就是 ?token=）。
"""

import time
import uuid
from typing import Any

import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

logger = structlog.get_logger("access")

# 降噪路径（健康探针每 10s 一次，静态资源每个请求一条）
_QUIET_PREFIXES = ("/api/v1/health", "/assets/", "/favicon.ico")
_REQUEST_ID_HEADER = "X-Request-ID"
_MAX_CLIENT_ID_LEN = 64


def _client_ip(request: Request) -> str:
    """客户端 IP：优先反代传来的 X-Real-IP / X-Forwarded-For 首跳。"""
    forwarded = request.headers.get("x-real-ip")
    if forwarded:
        return forwarded.strip()[:_MAX_CLIENT_ID_LEN]
    chain = request.headers.get("x-forwarded-for")
    if chain:
        return chain.split(",", 1)[0].strip()[:_MAX_CLIENT_ID_LEN]
    return request.client.host if request.client else "-"


class RequestContextMiddleware(BaseHTTPMiddleware):
    """为每个 HTTP 请求提供 request_id 与访问日志。"""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        incoming = request.headers.get(_REQUEST_ID_HEADER)
        # 只接受客户端传来的合理值，避免日志注入（换行/超长）
        request_id = (
            incoming.strip()[:64]
            if incoming and incoming.strip().isprintable() and len(incoming.strip()) <= 64
            else uuid.uuid4().hex[:16]
        )

        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            method=request.method,
            path=request.url.path,
        )

        started = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            response.headers[_REQUEST_ID_HEADER] = request_id
            return response
        except Exception:
            # 兜底的 500 handler 会再记一条；这里保留耗时与上下文
            logger.exception(
                "request_crashed",
                duration_ms=round((time.perf_counter() - started) * 1000, 1),
                client_ip=_client_ip(request),
            )
            raise
        finally:
            duration_ms = round((time.perf_counter() - started) * 1000, 1)
            if not request.url.path.startswith(_QUIET_PREFIXES):
                log: Any = logger
                log.info(
                    "request",
                    status=status_code,
                    duration_ms=duration_ms,
                    client_ip=_client_ip(request),
                )
            structlog.contextvars.clear_contextvars()
