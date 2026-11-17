"""数据库访问：async engine + session + Base。"""

import os
from collections.abc import AsyncGenerator
from contextlib import suppress

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from app.core.config import settings


class Base(DeclarativeBase):
    """ORM 声明基类。"""


# 测试环境（TESTING=1）使用 NullPool：每个连接独立创建/关闭，避免跨 event loop 复用。
# Celery worker 同理（DB_POOL=null）：worker 用 asyncio.run 每次新建事件循环，
# 连接池的连接绑定旧 loop 会报 "attached to a different loop"。
_is_worker_or_test = os.getenv("TESTING") == "1" or os.getenv("DB_POOL") == "null"
_pool_kwargs = (
    {"poolclass": NullPool}
    if _is_worker_or_test
    else {
        "pool_size": 10,
        "max_overflow": 20,
        "pool_pre_ping": True,
    }
)

engine = create_async_engine(settings.database_url, echo=False, **_pool_kwargs)

SessionLocal = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI 依赖：请求级 session。

    提交由端点显式执行一次（业务 + 审计同一事务，见 docs/code-audit-2026-11-17.md #9）。
    这里只负责失败善后：请求抛出任何异常时回滚，避免半截事务挂在连接上。
    """
    async with SessionLocal() as session:
        try:
            yield session
        except BaseException:
            with suppress(Exception):
                await session.rollback()
            raise
