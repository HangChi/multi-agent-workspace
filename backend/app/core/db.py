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


# 测试与 Celery worker 都用 NullPool：每个连接独立创建/关闭，避免跨 event loop 复用。
#
# 为什么不给测试上连接池（2026-11-20 实测过，已回退）：
# pytest.ini 把 test loop 设成 session 级，看起来可以池化，但 **WebSocket 测试
# 用的是另一个 event loop**（TestClient 的 portal）。池里的连接绑定在旧 loop，
# 关闭时报 `RuntimeError: Event loop is closed`，一次挂了 12 个 ws 用例。
# 顺带发现：池化还会把会话级状态（如某测试的 `SET search_path`）带给后续用例，
# 表现为「跑了几百个用例后突然 relation users does not exist」——
# 那条 `SET` 已修为 `SET LOCAL`（test_migration_0009），但结论不变：
# 测试隔离性比连接复用更重要。
#
# 代价是 ~2000 个用例各建各的连接，在本机经宿主端口映射连库时会偶发连接超时
# （`asyncpg... compat.timeout -> TimeoutError`，表现为随机几个用例失败、每次不同）。
# 该现象在 CI（service 容器 localhost 直连）上更罕见，目前用 `--reruns 2` 兜住，
# 见 .github/workflows/ci.yml。
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
