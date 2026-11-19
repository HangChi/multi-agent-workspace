"""邀请码管理端点（admin-only，015）。

挂在 /admin/invite-codes 下。三个动作：列表 / 发放 / 吊销。

为什么必须 admin：邀请码决定「谁能进这个系统、以什么角色进」，等于准入控制
本身。member 能发码的后果是任一人可以无限扩员，RBAC 形同虚设。
"""

from fastapi import APIRouter

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


@router.get("", response_model=InviteCodeList)
async def list_invite_codes(_user: CurrentUser, _admin: AdminUser, db: DB):
    rows = await invite_code_service.list_invite_codes(db)
    return {
        "items": [invite_code_service.to_out(row) for row in rows],
        "total": len(rows),
    }


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
    return invite_code_service.to_out(row)


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
    return {
        "items": [invite_code_service.to_out(row) for row in rows],
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
    return invite_code_service.to_out(row)
