"""安全基座：bcrypt 密码哈希 + JWT access/refresh 签发与校验。

token 载荷含 user_id / role / type / exp，对齐 contracts/api.md 的 Bearer JWT 约定。
密码哈希直接使用 bcrypt 库（passlib 已停止维护且与 bcrypt>=4.1 不兼容）。

⚠️ bcrypt 是**纯 CPU 密集**操作（当前 cost=12，实测单次 verify ≈174ms）。
在 async 端点里直接调用会阻塞整个事件循环——API 进程只有 2 个 worker，
几个并发登录就能让全站接口停顿数秒。因此异步代码路径必须走
`hash_password_async` / `verify_password_async`（内部丢到线程池），
同步版本只留给 CLI 脚本与测试夹具。
"""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import uuid4

import bcrypt
from jose import jwt

from app.core.config import settings


def hash_password(plain: str) -> str:
    """bcrypt 哈希密码（自动生成 salt，返回 $2b$ 前缀字符串）。

    同步阻塞；仅用于脚本/测试。异步代码请用 `hash_password_async`。
    """
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(plain.encode("utf-8"), salt).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """校验密码。

    同步阻塞；仅用于脚本/测试。异步代码请用 `verify_password_async`。
    """
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        return False


async def hash_password_async(plain: str) -> str:
    """`hash_password` 的异步版本：在线程池执行，不阻塞事件循环。"""
    return await asyncio.to_thread(hash_password, plain)


async def verify_password_async(plain: str, hashed: str) -> bool:
    """`verify_password` 的异步版本：在线程池执行，不阻塞事件循环。"""
    return await asyncio.to_thread(verify_password, plain, hashed)


def _create_token(
    *,
    user_id: str,
    role: str,
    token_type: Literal["access", "refresh"],
    ttl: timedelta,
) -> str:
    now = datetime.now(UTC)
    token_id = uuid4().hex
    payload: dict[str, Any] = {
        "sub": user_id,
        "user_id": user_id,
        "role": role,
        "type": token_type,
        # jti：refresh token 的轮换标识（服务端一次性消费，用后失效）
        "jti": token_id,
        "iat": now,
        "exp": now + ttl,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_access_token(user_id: str, role: str) -> str:
    """签发 access token（短时，默认 30 分钟）。"""
    return _create_token(
        user_id=user_id,
        role=role,
        token_type="access",
        ttl=timedelta(minutes=settings.jwt_access_ttl_minutes),
    )


def create_refresh_token(user_id: str, role: str) -> str:
    """签发 refresh token（长时，默认 7 天）。"""
    return _create_token(
        user_id=user_id,
        role=role,
        token_type="refresh",
        ttl=timedelta(days=settings.jwt_refresh_ttl_days),
    )


def decode_token(token: str) -> dict[str, Any]:
    """校验并解码 token，失败抛 JWTError。"""
    return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])


def create_token_pair(user_id: str, role: str) -> dict[str, str]:
    """签发 access + refresh 对。"""
    return {
        "access": create_access_token(user_id, role),
        "refresh": create_refresh_token(user_id, role),
        "token_type": "bearer",
    }
