"""认证服务：注册/登录/刷新/me（T022）。"""

from datetime import UTC, datetime

from jose import JWTError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.core.config import settings
from app.core.security import (
    create_token_pair,
    decode_token,
    hash_password,
    verify_password,
)
from app.core.token_store import consume_refresh_token, register_refresh_token
from app.models.invite_code import InviteCode
from app.models.user import User

_REFRESH_TTL_SECONDS = settings.jwt_refresh_ttl_days * 24 * 3600


def _refresh_jti(token: str) -> str | None:
    """从 refresh token 解出 jti（签发后立即登记用）。"""
    try:
        return decode_token(token).get("jti")
    except JWTError:  # 刚签发的 token 不应解码失败
        return None


async def _issue_tokens(user: User) -> dict[str, str]:
    """签发 token 对，并登记 refresh jti（轮换用）。"""
    tokens = create_token_pair(user.id, user.role)
    jti = _refresh_jti(tokens["refresh"])
    if jti:
        await register_refresh_token(jti, user.id, _REFRESH_TTL_SECONDS)
    return tokens


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

    # 消耗邀请码必须是单条原子语句：先读 used_count 再写回，两个并发注册会都读到
    # 旧值、各自 +1，最终只记一次，一次性邀请码因此能被用两次。条件更新同时把
    # 「是否用尽」的判断交给数据库。
    consumed = (
        await db.execute(
            update(InviteCode)
            .where(InviteCode.id == invite.id, InviteCode.used_count < InviteCode.max_uses)
            .values(used_count=InviteCode.used_count + 1)
            .returning(InviteCode.role)
        )
    ).first()
    if consumed is None:
        raise AppError("INVALID_INPUT", "邀请码已用尽")
    role = consumed[0]

    user = User(
        email=email,
        password_hash=hash_password(password),
        role=role,
        invite_code_id=invite.id,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    tokens = await _issue_tokens(user)
    return user, tokens


async def login(db: AsyncSession, *, email: str, password: str) -> tuple[User, dict[str, str]]:
    """登录：校验邮箱 + 密码 + 账号状态。"""
    user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if user is None or not verify_password(password, user.password_hash):
        raise AppError("FORBIDDEN", "邮箱或密码错误")
    if user.status != "active":
        raise AppError("FORBIDDEN", "账号已被禁用")
    tokens = await _issue_tokens(user)
    return user, tokens


async def refresh(db: AsyncSession, *, refresh_token: str) -> dict[str, str]:
    """刷新：消费旧 refresh（一次性）→ 签发新对（轮换）。

    旧 token 用后立即失效：泄露的 refresh token 只能被使用一次，
    攻击者与合法客户端会互相踢掉，异常刷新可被及时发现。
    """
    try:
        payload = decode_token(refresh_token)
    except JWTError:
        raise AppError("UNAUTHORIZED", "refresh token 无效或已过期")
    if payload.get("type") != "refresh":
        raise AppError("UNAUTHORIZED", "token 类型错误")

    jti = payload.get("jti")
    if not jti:
        raise AppError("UNAUTHORIZED", "refresh token 缺少标识，请重新登录")
    if not await consume_refresh_token(jti, grace_seconds=settings.refresh_grace_seconds):
        # 已使用且超出宽限期：可能是重放，也可能是被撤销
        raise AppError("UNAUTHORIZED", "refresh token 已失效，请重新登录")

    user_id = payload.get("user_id")
    user = await db.get(User, user_id)
    if user is None or user.status != "active":
        raise AppError("UNAUTHORIZED", "用户不存在或已禁用")
    return await _issue_tokens(user)
