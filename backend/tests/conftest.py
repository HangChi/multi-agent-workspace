"""测试基座：async 引擎、测试数据库、httpx client、JWT 辅助、用户工厂。

用法：
- 集成测试导入 `client`（httpx AsyncClient，走真实 FastAPI 应用）与 `db_session`。
- `make_user()` 工厂快速创建用户并返回 (user, password)。
- `make_headers(user)` 生成带 Bearer access token 的请求头。

⚠️ 环境变量必须在导入 app 模块之前设置：`app.core.db` 在模块加载时
就创建 engine，若此时 DATABASE_URL 未指向测试库，client 请求会打到开发库。
"""

import os
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


def _local_stack_database_url() -> str | None:
    """从 deploy/.env 取本地栈的连接串（容器内主机名换成本机地址）。

    为什么不再写死口令：默认值曾是 `aw:aw-dev-password@localhost`，但本地
    compose 栈的口令由 deploy/.env 的 POSTGRES_PASSWORD 决定（实测是
    test-password-123）。两者不一致的后果是「不带环境变量直接 pytest」
    秒挂 12~1034 个 fixture error，而且报错长得像代码坏了——每个新人
    都会踩一次，每次都要人告诉他一串 export。
    """
    env_file = Path(__file__).resolve().parents[2] / "deploy" / ".env"
    if not env_file.is_file():
        return None
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("DATABASE_URL="):
            value = line.split("=", 1)[1].strip().strip('"').strip("'")
            parts = urlsplit(value)
            if parts.hostname in {"postgres", "db"}:
                # 容器网络名在宿主上解析不了：换成回环地址，端口与凭据不变
                cred = (
                    parts.netloc.rsplit("@", 1)[0] + "@" if "@" in parts.netloc else ""
                )
                port = f":{parts.port}" if parts.port else ""
                parts = parts._replace(netloc=f"{cred}127.0.0.1{port}")
            return urlunsplit(parts)
    return None


# 测试标记 + 数据库 URL：先于任何 app 模块导入设置
os.environ["TESTING"] = "1"
_TEST_DATABASE_URL = (
    os.getenv("DATABASE_URL")
    or _local_stack_database_url()
    or "postgresql+asyncpg://aw:aw-dev-password@127.0.0.1:5432/agent_workbench"
)
if "agent_workbench_test" not in _TEST_DATABASE_URL:
    _TEST_DATABASE_URL = _TEST_DATABASE_URL.replace(
        "agent_workbench", "agent_workbench_test"
    )
os.environ["DATABASE_URL"] = _TEST_DATABASE_URL


def _isolate_test_redis() -> None:
    """把测试从开发栈的 Redis db 0 上挪开（0 → 15）。

    为什么必须隔离：本地 `docker compose` 的 api/worker 就在 db 0 上存会话、
    限流计数、分布式锁、通知游标。测试若也用 db 0，双方会互相踩：

    - 开发栈写的 rate-limit 计数让测试偶发 429；
    - 测试清理 keyspace 又把开发栈的锁/会话抹掉；
    - 结果是「同一份代码，单文件跑绿、全量跑随机红」，而且**每次失败的用例
      都不一样**（实测 3.15 上三次全量分别红 6 / 3 / 1 个，互不重复）。

    CI 里是独立的 redis service，所以这个问题只在本地出现 —— 也正因如此，
    它长期被误判成「测试本身不稳」。

    规则：只在 url 指向 db 0 时才改。用户显式指定别的库（比如 CI 的 db 9）
    就尊重原值，不猜。
    """
    url = os.getenv("REDIS_URL")
    if not url:
        # 没给就用与 app.core.config 默认值同源的主机，只换库号
        os.environ["REDIS_URL"] = "redis://127.0.0.1:6379/15"
        return
    parts = urlsplit(url)
    db = (parts.path or "/0").strip("/") or "0"
    if db != "0":
        return
    os.environ["REDIS_URL"] = urlunsplit(parts._replace(path="/15"))


_isolate_test_redis()

from collections.abc import AsyncGenerator, Callable
from dataclasses import asdict
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
from app.executors.catalog import CATALOG
from app.models.tool import Tool
from app.models.user import User

# 沙箱相关夹具（T017）：fake_docker / sbx_semaphore / sandbox_factory / fixed_clock。
# 用 `pytest_plugins` 而不是 import：fixture 需要在 conftest 作用域内注册才能被
# 所有测试目录看到。
pytest_plugins = ["tests.sandbox_fixtures"]

# 测试数据库 URL：环境变量已指向测试库（见文件头部），直接复用，避免二次 replace 出错
TEST_DATABASE_URL = settings.database_url

# NullPool：每个连接独立创建/关闭，避免跨 event loop 复用连接
engine = create_async_engine(TEST_DATABASE_URL, echo=False, poolclass=NullPool)
TestSession = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
)


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _setup_test_db() -> AsyncGenerator[None, None]:
    """会话级：重建测试库表结构（create_all + 清理）。"""
    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
        await conn.run_sync(Base.metadata.create_all)
        # Match the production migration's explicit enabled catalog. Missing rows
        # now correctly fail closed instead of silently granting code defaults.
        await conn.execute(
            Tool.__table__.insert(),
            [{**asdict(spec), "enabled": True} for spec in CATALOG.values()],
        )
    yield
    await engine.dispose()


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _reset_login_throttle() -> AsyncGenerator[None, None]:
    """会话级：清掉 Redis 里残留的登录限流计数。

    为什么需要：登录防爆破把失败次数记在 Redis（不在测试库里），而
    `_setup_test_db` 只重建 PostgreSQL，Redis 从来没人清理。于是
    `login_fail:ip:<固定测试IP>`、`login_fail:acct:<固定邮箱>|<IP>`
    会**跨 pytest 进程累积**：第一次跑全绿，隔一会儿重跑就撞上 429。

    这个缺陷很隐蔽 —— 单用例看都是对的（随机邮箱做隔离），只有整库重跑
    才暴露，而且表现成「跟本次改动毫不相关的 4 个失败」。

    这里在会话开始时清一次；用例内仍用随机邮箱自保（见
    test_login_rate_limit.py 的 _unique_email），两者互补：
    会话级清理保证可重复，用例级隔离保证互不干扰。
    """
    import redis.asyncio as aioredis

    from app.core.config import settings

    client = aioredis.from_url(settings.redis_url, decode_responses=True)
    try:
        # scan_iter 而非 keys：避免在大 keyspace 上阻塞 Redis
        stale = [key async for key in client.scan_iter("login_fail:*")]
        if stale:
            await client.delete(*stale)
    except Exception as exc:  # noqa: BLE001
        # 没有 Redis 时（纯单测）不该让整个测试会话失败；
        # 需要限流的集成测试会自己暴露连接问题。
        print(f"[conftest] 跳过登录限流计数清理：{type(exc).__name__}")
    finally:
        try:
            await client.aclose()
        except Exception as exc:  # noqa: BLE001
            # 关闭失败不影响测试结论（连接可能本就没建立成功）
            print(f"[conftest] 关闭 Redis 连接失败：{type(exc).__name__}")
    yield


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

    counter: int = 0

    async def _make_user(
        email: str | None = None,
        password: str = "TestPass123!",
        role: str = "member",
        status: str = "active",
    ) -> tuple[User, str]:
        nonlocal counter
        counter += 1
        user = User(
            email=email or f"u{counter}-{uuid.uuid4().hex[:8]}@test.com",
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
def auth_headers(
    make_user: Callable[..., Any], make_headers: Callable[..., Any]
) -> Callable[..., Any]:
    """别名：直接返回 (headers, user) 便于测试断言归属。"""

    async def _auth_headers(role: str = "member") -> tuple[dict[str, str], User]:
        user, _ = await make_user(role=role)
        headers = await make_headers(user=user)
        return headers, user

    return _auth_headers
