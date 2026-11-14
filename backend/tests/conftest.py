"""测试基座：async 引擎、测试数据库、httpx client、JWT 辅助、用户工厂。

用法：
- 集成测试导入 `client`（httpx AsyncClient，走真实 FastAPI 应用）与 `db_session`。
- `make_user()` 工厂快速创建用户并返回 (user, password)。
- `make_headers(user)` 生成带 Bearer access token 的请求头。
"""

from collections.abc import AsyncGenerator, Callable
from typing import Any

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.core.db import Base
from app.core.security import create_access_token, hash_password
from app.models.user import User

# 测试数据库 URL：独立库，避免污染开发库
TEST_DATABASE_URL = settings.database_url.replace(
    "agent_workbench", "agent_workbench_test"
)

# NullPool：每个连接独立创建/关闭，避免跨 event loop 复用连接
engine = create_async_engine(TEST_DATABASE_URL, echo=False, poolclass=NullPool)
TestSession = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False, autoflush=False)


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _setup_test_db() -> AsyncGenerator[None, None]:
    """会话级：重建测试库表结构（create_all + 清理）。"""
    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
        await conn.run_sync(Base.metadata.create_all)
    yield
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """每个测试一个 session，测试后回滚。"""
    async with TestSession() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture
async def client() -> AsyncGenerator[httpx.AsyncClient, None]:
    """真实 FastAPI 应用 + httpx AsyncClient（走测试库）。"""
    from app.main import app  # 延迟导入，避免循环依赖

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as c:
        yield c


@pytest.fixture
def make_user(db_session: AsyncSession) -> Callable[..., Any]:
    """用户工厂：创建用户返回 (user, plain_password)。"""

    async def _make_user(
        email: str | None = None,
        password: str = "TestPass123!",
        role: str = "member",
        status: str = "active",
    ) -> tuple[User, str]:
        user = User(
            email=email or f"u{hash(email) & 0xFFFF}@test.com",
            password_hash=hash_password(password),
            role=role,
            status=status,
        )
        db_session.add(user)
        await db_session.commit()
        return user, password

    return _make_user


@pytest.fixture
def make_headers(make_user: Callable[..., Any]) -> Callable[..., dict[str, str]]:
    """生成带 Bearer token 的请求头。"""

    async def _make_headers(
        user: User | None = None, role: str = "member"
    ) -> dict[str, str]:
        if user is None:
            user, _ = await make_user(role=role)
        token = create_access_token(user.id, user.role)
        return {"Authorization": f"Bearer {token}"}

    return _make_headers


@pytest.fixture
def auth_headers(make_headers: Callable[..., Any]) -> Callable[..., dict[str, str]]:
    """别名：直接返回 (headers, user) 便于测试断言归属。"""

    async def _auth_headers(role: str = "member") -> tuple[dict[str, str], User]:
        user, _ = await make_user(role=role)
        headers = await make_headers(user=user)
        return headers, user

    return _auth_headers
