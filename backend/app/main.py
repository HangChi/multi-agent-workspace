"""FastAPI 应用入口。

注册：CORS、统一错误处理、/api/v1 健康检查（存活 + 就绪）、业务路由。
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from sqlalchemy import text

from app.api import errors
from app.api.middleware import RequestContextMiddleware
from app.api.v1.admin import router as admin_router
from app.api.v1.admin_egress import router as admin_egress_router
from app.api.v1.agent_egress import router as agent_egress_router
from app.api.v1.agents import router as agents_router
from app.api.v1.auth import router as auth_router
from app.api.v1.external_observability import router as external_observability_router
from app.api.v1.external_tool_grants import router as external_tool_grants_router
from app.api.v1.external_tool_servers import router as external_tool_servers_router
from app.api.v1.groups import router as groups_router
from app.api.v1.marketplace import router as marketplace_router
from app.api.v1.requests import router as requests_router
from app.api.v1.runs import router as runs_router
from app.api.v1.schedules import router as schedules_router
from app.api.v1.stats import router as stats_router
from app.api.v1.tools import router as tools_router
from app.api.v1.ws import router as ws_router
from app.core import metrics
from app.core.config import settings
from app.core.db import SessionLocal
from app.core.events import redis_ping
from app.core.log_setup import configure_logging
from app.core.redis import close_redis, get_broker_redis, get_redis

configure_logging()

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """关停时释放进程级 Redis 连接池（单例，不释放会留到进程被 kill）。"""
    try:
        yield
    finally:
        await close_redis()


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        lifespan=_lifespan,
        # 生产关闭交互式文档：避免对外暴露完整 API 结构
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None if settings.is_production else "/redoc",
        openapi_url=None if settings.is_production else "/openapi.json",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )
    # 后添加的更外层：request_id 需覆盖 CORS 预检等所有请求
    app.add_middleware(RequestContextMiddleware)

    errors.register_error_handlers(app)

    api_v1 = APIRouter(prefix="/api/v1")
    api_v1.include_router(auth_router)
    api_v1.include_router(agents_router)
    api_v1.include_router(agent_egress_router)
    api_v1.include_router(groups_router)
    api_v1.include_router(marketplace_router)
    api_v1.include_router(external_tool_servers_router)
    api_v1.include_router(external_tool_grants_router)
    api_v1.include_router(external_observability_router)
    api_v1.include_router(requests_router)
    api_v1.include_router(schedules_router)
    api_v1.include_router(runs_router)
    api_v1.include_router(stats_router)
    api_v1.include_router(tools_router)
    api_v1.include_router(admin_router)
    api_v1.include_router(admin_egress_router)
    app.include_router(api_v1)
    app.include_router(ws_router)

    @app.get("/api/v1/health")
    async def health() -> dict:
        """存活检查（liveness）：进程能响应即可，供容器重启判定。"""
        return {"status": "ok", "app": settings.app_name}

    @app.get("/api/v1/health/ready")
    async def readiness() -> JSONResponse:
        """就绪检查（readiness）：DB + Redis 均可达才算就绪。

        此前只有 /health 只返回 ok：DB 挂了容器仍报 healthy，请求全 500，
        编排层无法感知。这里逐个探测依赖并返回 503。
        """
        checks: dict[str, str] = {}
        ok = True

        try:
            async with SessionLocal() as db:
                await db.execute(text("SELECT 1"))
            checks["database"] = "ok"
        except Exception as exc:  # noqa: BLE001 - 就绪探测需捕获全部异常
            checks["database"] = f"error: {type(exc).__name__}"
            ok = False

        try:
            await redis_ping()
            checks["redis"] = "ok"
        except Exception as exc:  # noqa: BLE001
            checks["redis"] = f"error: {type(exc).__name__}"
            ok = False

        return JSONResponse(
            status_code=200 if ok else 503,
            content={"status": "ok" if ok else "unavailable", "checks": checks},
        )

    @app.get("/metrics", include_in_schema=False)
    async def prometheus_metrics(request: Request) -> Response:
        """Prometheus 抓取端点（文本格式）。

        刻意不挂在 /api/v1 下：nginx 只反代 /api 与 /ws，因此这个端点默认不对外，
        由 compose 网络内的 Prometheus 直连 api:8000 抓取。需要更严的隔离时设
        `METRICS_TOKEN`，抓取侧带 `X-Metrics-Token`。
        """
        if not settings.metrics_enabled:
            raise HTTPException(status_code=404, detail="指标未启用")
        if settings.metrics_token and (
            request.headers.get("x-metrics-token") != settings.metrics_token
        ):
            raise HTTPException(status_code=401, detail="指标凭据无效")

        # 队列深度是"抓取时才知道"的量（不是应用计数），失败不能影响抓取
        try:
            depth = await get_broker_redis().llen(settings.celery_queue_name)
            metrics.CELERY_QUEUE_DEPTH.set(float(depth))
        except Exception as exc:  # noqa: BLE001 - 指标端点的可用性优先于准确性
            logger.warning("queue_depth_unavailable", error=type(exc).__name__)

        # 指标是进程内的：API 有 2 个 uvicorn worker，Celery 又是独立容器。
        # 只渲染本进程会漏掉一半 API 请求，以及**全部** Worker 侧指标
        # （LLM 调用、沙箱执行、Run 终态）——那些告警将永不触发。
        # 因此把本进程快照与各 Worker 定期发布到 Redis 的快照相加。
        snapshots = [metrics.snapshot()]
        try:
            snapshots.extend(await metrics.load_remote_snapshots(get_redis()))
        except Exception as exc:  # noqa: BLE001 - 退化路径：只报本进程
            logger.warning("metrics_remote_unavailable", error=type(exc).__name__)

        return Response(
            content=metrics.render_merged(metrics.merge_snapshots(snapshots)),
            media_type="text/plain; version=0.0.4",
        )

    return app


app = create_app()
