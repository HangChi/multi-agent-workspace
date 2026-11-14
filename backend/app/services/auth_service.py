"""认证服务：注册/登录/刷新/me（T022）。"""

from datetime import UTC, datetime

from jose import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.core.security import (
    create_token_pair,
    decode_token,
    hash_password,
    verify_password,
)
from app.models.invite_code import InviteCode
from app.models.user import User


async def register(
    db: AsyncSession,
    *,
    email: str,
    password: str,
    invite_code: str,
) -> tuple[User, dict[str, str]]:
    """注册：校验邀请码（未过期、未用尽）→ 事务内消耗 → 建用户 → 签发 token。"""
    invite = (
        await db.execute(select(InviteCode).where(InviteCode.code == invite_code))
    ).scalar_one_or_none()
    if invite is None:
        raise AppError("INVALID_INPUT", "邀请码无效")
    if invite.expires_at is not None and invite.expires_at < datetime.now(UTC):
        raise AppError("INVALID_INPUT", "邀请码已过期")
    if invite.used_count >= invite.max_uses:
        raise AppError("INVALID_INPUT", "邀请码已用尽")

    existing = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if existing is not None:
        raise AppError("CONFLICT", "邮箱已被注册")

    # 事务内：消耗邀请码 + 建用户
    invite.used_count += 1
    user = User(
        email=email,
        password_hash=hash_password(password),
        role=invite.role,
        invite_code_id=invite.id,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    tokens = create_token_pair(user.id, user.role)
    return user, tokens


async def login(db: AsyncSession, *, email: str, password: str) -> tuple[User, dict[str, str]]:
    """登录：校验邮箱 + 密码 + 账号状态。"""
    user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if user is None or not verify_password(password, user.password_hash):
        raise AppError("FORBIDDEN", "邮箱或密码错误")
    if user.status != "active":
        raise AppError("FORBIDDEN", "账号已被禁用")
    tokens = create_token_pair(user.id, user.role)
    return user, tokens


async def refresh(db: AsyncSession, *, refresh_token: str) -> dict[str, str]:
    """刷新：校验 refresh token → 重新签发 access + refresh（轮换）。"""
    try:
        payload = decode_token(refresh_token)
    except JWTError:
        raise AppError("UNAUTHORIZED", "refresh token 无效或已过期")
    if payload.get("type") != "refresh":
        raise AppError("UNAUTHORIZED", "token 类型错误")
    user_id = payload.get("user_id")
    user = await db.get(User, user_id)
    if user is None or user.status != "active":
        raise AppError("UNAUTHORIZED", "用户不存在或已禁用")
    return create_token_pair(user.id, user.role)
