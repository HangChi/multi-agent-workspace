"""FastAPI 应用入口。

当前注册：CORS、统一错误处理、/api/v1 健康检查。
后续 Phase 按需挂载 auth / agents / groups / requests / runs 等路由。
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import errors
from app.core.config import settings


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    errors.register_error_handlers(app)

    @app.get("/api/v1/health")
    async def health() -> dict:
        return {"status": "ok", "app": settings.app_name}

    return app


app = create_app()
