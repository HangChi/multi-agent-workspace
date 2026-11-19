"""邀请码发放与吊销（015）。

为什么要有这层：邀请码此前只有「消费端」（注册时校验），没有管理端。
默认码用尽之后，唯一的续发方式是用 SQL 往 invite_codes 里插一行——
2026-11-19 实测就撞上了（默认码 10/10 用尽，注册直接 400）。

设计取舍：
- **不删除，只吊销**：users.invite_code_id 引用邀请码，删行会断掉「这个人
  是从哪张码注册进来的」审计链。吊销 = 打时间戳，注册校验直接判无效。
- **码值可读性**：去掉 I/O/0/1 这类易混字符，管理员要在聊天里口头/手抄转发。
- **不落库明文以外的副本**：码本身就是通行证，只此一处；泄露的处置手段是
  吊销 + 重新发一张。
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.models.invite_code import InviteCode
from app.schemas.invite_code import InviteCodeOut, invite_code_state

# 去掉 I / O / 0 / 1：手抄与口头转发时最容易出错
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_CODE_LEN = 12


def _generate_code() -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(_CODE_LEN))


def to_out(row: InviteCode) -> InviteCodeOut:
    return InviteCodeOut(
        id=row.id,
        code=row.code,
        role=row.role,
        max_uses=row.max_uses,
        used_count=row.used_count,
        expires_at=row.expires_at,
        revoked_at=row.revoked_at,
        created_at=row.created_at,
        state=invite_code_state(
            revoked_at=row.revoked_at,
            expires_at=row.expires_at,
            used_count=row.used_count,
            max_uses=row.max_uses,
        ),
    )


async def list_invite_codes(db: AsyncSession) -> list[InviteCode]:
    """全部邀请码，新发的在前（管理员刚发的要能立刻看到）。"""
    rows = await db.execute(
        select(InviteCode).order_by(InviteCode.created_at.desc()).limit(200)
    )
    return list(rows.scalars().all())


async def create_invite_code(
    db: AsyncSession,
    *,
    role: str,
    max_uses: int,
    expires_in_days: int | None,
    created_by: str,
) -> InviteCode:
    """发一张新码。码值随机生成，撞库时重试（32^12 空间，实际不会撞）。"""
    expires_at = (
        datetime.now(UTC) + timedelta(days=expires_in_days)
        if expires_in_days is not None
        else None
    )
    for _ in range(5):
        code = _generate_code()
        exists = (
            await db.execute(select(InviteCode.id).where(InviteCode.code == code))
        ).scalar_one_or_none()
        if exists is None:
            break
    else:  # pragma: no cover - 32^12 空间下不可能走到
        raise AppError("INTERNAL_ERROR", "邀请码生成冲突，请重试")

    row = InviteCode(
        code=code,
        role=role,
        max_uses=max_uses,
        used_count=0,
        expires_at=expires_at,
        created_by=created_by,
    )
    db.add(row)
    await db.flush()
    await db.refresh(row)
    return row


async def revoke_invite_code(db: AsyncSession, code_id: str) -> InviteCode:
    """吊销一张码。已吊销的再吊销是 409（幂等留给客户端自己判断）。"""
    row = await db.get(InviteCode, code_id)
    if row is None:
        raise AppError("NOT_FOUND", "邀请码不存在")
    if row.revoked_at is not None:
        raise AppError("CONFLICT", "邀请码已吊销")
    row.revoked_at = datetime.now(UTC)
    await db.flush()
    await db.refresh(row)
    return row
