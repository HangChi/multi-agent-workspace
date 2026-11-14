"""FastAPI 应用入口。

注册：CORS、统一错误处理、/api/v1 健康检查（存活 + 就绪）、业务路由。
"""

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api import errors
from app.api.middleware import RequestContextMiddleware
from app.api.v1.admin import router as admin_router
from app.api.v1.admin_egress import router as admin_egress_router
from app.api.v1.agent_egress import router as agent_egress_router
from app.api.v1.agents import router as agents_router
from app.api.v1.auth import router as auth_router
from app.api.v1.groups import router as groups_router
from app.api.v1.requests import router as requests_router
from app.api.v1.runs import router as runs_router
from app.api.v1.stats import router as stats_router
from app.api.v1.tools import router as tools_router
from app.api.v1.ws import router as ws_router
from app.core.config import settings
from app.core.db import SessionLocal
from app.core.events import redis_ping
from app.core.log_setup import configure_logging

configure_logging()


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
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
    api_v1.include_router(requests_router)
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

    return app


app = create_app()
