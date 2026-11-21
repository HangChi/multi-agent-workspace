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

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.models.department import Department
from app.models.invite_code import InviteCode
from app.schemas.invite_code import InviteCodeOut, invite_code_state

# 去掉 I / O / 0 / 1：手抄与口头转发时最容易出错
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_CODE_LEN = 12


def _generate_code() -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(_CODE_LEN))


def to_out(row: InviteCode, department_name: str | None = None) -> InviteCodeOut:
    return InviteCodeOut(
        id=row.id,
        code=row.code,
        role=row.role,
        max_uses=row.max_uses,
        used_count=row.used_count,
        expires_at=row.expires_at,
        revoked_at=row.revoked_at,
        created_at=row.created_at,
        department_id=row.department_id,
        department_name=department_name,
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


async def _assert_department_quota(
    db: AsyncSession, department_id: str | None, adding_seats: int
) -> None:
    """部门本月名额校验（022）。

    「本月」按 `created_at` 的自然月；**已撤销的码也计入**——发出去就占用，
    否则「发了再撤销」能绕过上限。配额与「已撤销」是两个概念：前者是预算，
    后者是这张码还能不能用。
    """
    if department_id is None:
        return
    dept = await db.get(Department, department_id)
    if dept is None:
        raise AppError("NOT_FOUND", "部门不存在")
    if dept.invite_quota is None:
        return
    month_start = datetime.now(UTC).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )
    issued = int(
        (
            await db.execute(
                select(func.coalesce(func.sum(InviteCode.max_uses), 0)).where(
                    InviteCode.department_id == department_id,
                    InviteCode.created_at >= month_start,
                )
            )
        ).scalar_one()
    )
    if issued + adding_seats > dept.invite_quota:
        raise AppError(
            "CONFLICT",
            f"部门本月邀请名额不足：已发 {issued} / 上限 {dept.invite_quota}",
        )


async def create_invite_code(
    db: AsyncSession,
    *,
    role: str,
    max_uses: int,
    expires_in_days: int | None,
    created_by: str,
    department_id: str | None = None,
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

    await _assert_department_quota(db, department_id, max_uses)
    row = InviteCode(
        code=code,
        role=role,
        max_uses=max_uses,
        used_count=0,
        expires_at=expires_at,
        created_by=created_by,
        department_id=department_id,
    )
    db.add(row)
    await db.flush()
    await db.refresh(row)
    return row


async def create_invite_codes(
    db: AsyncSession,
    *,
    count: int,
    role: str,
    max_uses: int,
    expires_in_days: int | None,
    created_by: str,
    department_id: str | None = None,
) -> list[InviteCode]:
    """批量发码（1..20 张，同一批参数）。

    022：整批按「count × max_uses」一次性校验部门配额——逐张校验会在中途
    超限，留下半批码（更难解释）。

    为什么批量：给一个新小组开三个人是常见动作，逐个点「生成」再逐个复制
    既慢又容易漏发。上限 20 是为了让「一键发 500 张」这种误操作不存在。
    """
    await _assert_department_quota(db, department_id, count * max_uses)
    return [
        await create_invite_code(
            db,
            role=role,
            max_uses=max_uses,
            expires_in_days=expires_in_days,
            created_by=created_by,
        )
        for _ in range(count)
    ]


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


# ---------------------------------------------------------------- 运营统计（022）


async def invite_stats(db: AsyncSession) -> dict:
    """邀请码运营总览：总量 / 按角色 / 按部门 / 使用最多的码。

    一次聚合查询族，不逐码查用户（N+1）。「已发名额」= SUM(max_uses)，
    「已用名额」= SUM(used_count)——两个口径分开，否则「发了 100 个名额用了 3 个」
    和「发了 3 个用了 3 个」看起来一样。
    """
    month_start = datetime.now(UTC).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )

    totals = (
        await db.execute(
            select(
                func.count(),
                func.coalesce(func.sum(InviteCode.max_uses), 0),
                func.coalesce(func.sum(InviteCode.used_count), 0),
            )
        )
    ).one()

    by_role = (
        await db.execute(
            select(
                InviteCode.role,
                func.count(),
                func.coalesce(func.sum(InviteCode.max_uses), 0),
                func.coalesce(func.sum(InviteCode.used_count), 0),
            ).group_by(InviteCode.role)
        )
    ).all()

    dept_rows = (
        await db.execute(
            select(
                Department.id,
                Department.name,
                Department.invite_quota,
                func.coalesce(func.sum(InviteCode.max_uses), 0),
                func.coalesce(func.sum(InviteCode.used_count), 0),
            )
            .join(InviteCode, InviteCode.department_id == Department.id, isouter=True)
            .where(
                (InviteCode.department_id.is_(None))
                | (InviteCode.created_at >= month_start)
            )
            .group_by(Department.id, Department.name, Department.invite_quota)
            .order_by(Department.path)
        )
    ).all()

    top = (
        await db.execute(
            select(InviteCode)
            .where(InviteCode.used_count > 0)
            .order_by(InviteCode.used_count.desc())
            .limit(5)
        )
    ).scalars()

    return {
        "total_codes": int(totals[0]),
        "issued_seats": int(totals[1]),
        "used_seats": int(totals[2]),
        "by_role": [
            {
                "role": role,
                "codes": int(codes),
                "issued_seats": int(seats),
                "used_seats": int(used),
            }
            for role, codes, seats, used in by_role
        ],
        "by_department": [
            {
                "department_id": dept_id,
                "department_name": name,
                "quota": quota,
                # 本月已发名额（配额校验用的同一口径）
                "issued_seats_this_month": int(seats),
                "used_seats_this_month": int(used),
            }
            for dept_id, name, quota, seats, used in dept_rows
        ],
        "top_codes": [
            {
                "code": row.code,
                "role": row.role,
                "max_uses": row.max_uses,
                "used_count": row.used_count,
            }
            for row in top
        ],
    }
