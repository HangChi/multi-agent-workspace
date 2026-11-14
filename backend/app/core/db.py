"""数据库访问：async engine + session + Base。"""

import os
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from app.core.config import settings


class Base(DeclarativeBase):
    """ORM 声明基类。"""


# 测试环境（TESTING=1）使用 NullPool：每个连接独立创建/关闭，避免跨 event loop 复用
_pool_kwargs = {"poolclass": NullPool} if os.getenv("TESTING") == "1" else {
    "pool_size": 10,
    "max_overflow": 20,
    "pool_pre_ping": True,
}

engine = create_async_engine(settings.database_url, echo=False, **_pool_kwargs)

SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False, autoflush=False)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI 依赖：请求级 session。"""
    async with SessionLocal() as session:
        yield session
