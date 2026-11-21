"""邀请码管理端点（admin-only，015）。

挂在 /admin/invite-codes 下。三个动作：列表 / 发放 / 吊销。

为什么必须 admin：邀请码决定「谁能进这个系统、以什么角色进」，等于准入控制
本身。member 能发码的后果是任一人可以无限扩员，RBAC 形同虚设。
"""

from fastapi import APIRouter
from sqlalchemy import select

from app.api.deps import DB, AdminUser, CurrentUser
from app.core.audit import write_audit
from app.schemas.invite_code import (
    InviteCodeBatchCreate,
    InviteCodeCreate,
    InviteCodeList,
    InviteCodeOut,
)
from app.services import invite_code_service

router = APIRouter(prefix="/admin/invite-codes", tags=["admin-invite-codes"])


async def _department_names(db) -> dict[str, str]:
    """部门 id → 名称（列表/统计展示用，一次查询）。"""
    from app.models.department import Department

    rows = (await db.execute(select(Department.id, Department.name))).all()
    return {dept_id: name for dept_id, name in rows}


@router.get("", response_model=InviteCodeList)
async def list_invite_codes(_user: CurrentUser, _admin: AdminUser, db: DB):
    rows = await invite_code_service.list_invite_codes(db)
    names = await _department_names(db)
    return {
        "items": [
            invite_code_service.to_out(
                row, names.get(row.department_id) if row.department_id else None
            )
            for row in rows
        ],
        "total": len(rows),
    }


@router.get("/stats", response_model=dict)
async def invite_code_stats(_user: CurrentUser, _admin: AdminUser, db: DB):
    """邀请码运营总览（022）：总量 / 按角色 / 按部门（含本月已发与配额）/ 用最多的码。

    与列表分开：列表回答「有哪些码」，统计回答「招人进度与预算还剩多少」——
    两个问题的刷新频率与受众不同（统计页给管理者看趋势）。
    """
    return await invite_code_service.invite_stats(db)


@router.post("", response_model=InviteCodeOut, status_code=201)
async def create_invite_code(
    payload: InviteCodeCreate, user: CurrentUser, _admin: AdminUser, db: DB
):
    row = await invite_code_service.create_invite_code(
        db,
        role=payload.role,
        max_uses=payload.max_uses,
        expires_in_days=payload.expires_in_days,
        created_by=user.id,
        department_id=str(payload.department_id) if payload.department_id else None,
    )
    # 审计落同一事务（write_audit 不自己 commit，见 docs/code-audit #9）
    await write_audit(
        db,
        user_id=user.id,
        action="invite_code_create",
        target_type="invite_code",
        target_id=row.id,
        detail={"role": payload.role, "max_uses": payload.max_uses},
    )
    await db.commit()
    await db.refresh(row)
    names = await _department_names(db)
    return invite_code_service.to_out(
        row, names.get(row.department_id) if row.department_id else None
    )


@router.post("/batch", response_model=InviteCodeList, status_code=201)
async def create_invite_codes_batch(
    payload: InviteCodeBatchCreate, user: CurrentUser, _admin: AdminUser, db: DB
):
    """一次发多张（1..20）。给新小组开几个人是常见动作，逐个点太笨。"""
    rows = await invite_code_service.create_invite_codes(
        db,
        count=payload.count,
        role=payload.role,
        max_uses=payload.max_uses,
        expires_in_days=payload.expires_in_days,
        created_by=user.id,
        department_id=str(payload.department_id) if payload.department_id else None,
    )
    await write_audit(
        db,
        user_id=user.id,
        action="invite_code_create",
        target_type="invite_code",
        target_id=rows[0].id,
        detail={
            "role": payload.role,
            "max_uses": payload.max_uses,
            "count": payload.count,
            "batch": True,
        },
    )
    await db.commit()
    for row in rows:
        await db.refresh(row)
    names = await _department_names(db)
    return {
        "items": [
            invite_code_service.to_out(
                row, names.get(row.department_id) if row.department_id else None
            )
            for row in rows
        ],
        "total": len(rows),
    }


@router.post("/{code_id}/revoke", response_model=InviteCodeOut)
async def revoke_invite_code(
    code_id: str, user: CurrentUser, _admin: AdminUser, db: DB
):
    row = await invite_code_service.revoke_invite_code(db, code_id)
    await write_audit(
        db,
        user_id=user.id,
        action="invite_code_revoke",
        target_type="invite_code",
        target_id=row.id,
    )
    await db.commit()
    await db.refresh(row)
    names = await _department_names(db)
    return invite_code_service.to_out(
        row, names.get(row.department_id) if row.department_id else None
    )
