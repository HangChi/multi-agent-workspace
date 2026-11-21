"""邀请码申请端点（027）。

- `POST /invite-requests`            member 提申请
- `GET  /invite-requests?status=`    admin 看队列 / 成员看自己的
- `POST /invite-requests/{id}/approve`  admin 批准并发码（继承 022 的部门配额）
- `POST /invite-requests/{id}/reject`   admin 拒绝

为什么成员也能 GET：他要知道「我申请的那个批了没」。返回体里 admin 能看到
全部、成员只看自己的（同 007 的 relevant/mine 纪律）。
"""

from fastapi import APIRouter, Query, status
from sqlalchemy import select

from app.api.deps import DB, AdminUser, CurrentUser, MemberUser, ResourceId
from app.core.audit import write_audit
from app.models.department import Department
from app.models.user import User
from app.schemas.invite_code import (
    InviteRequestCreate,
    InviteRequestList,
    InviteRequestOut,
    InviteRequestReject,
)
from app.services import invite_request_service

router = APIRouter(prefix="/invite-requests", tags=["invite-requests"])


async def _project(db, rows) -> list[InviteRequestOut]:
    user_ids = {r.requester_id for r in rows}
    emails = (
        dict(
            (
                await db.execute(
                    select(User.id, User.email).where(User.id.in_(user_ids))
                )
            ).all()
        )
        if user_ids
        else {}
    )
    dept_ids = {r.department_id for r in rows if r.department_id}
    depts = (
        dict(
            (
                await db.execute(
                    select(Department.id, Department.name).where(
                        Department.id.in_(dept_ids)
                    )
                )
            ).all()
        )
        if dept_ids
        else {}
    )

    out: list[InviteRequestOut] = []
    for row in rows:
        item = InviteRequestOut.model_validate(row)
        item.requester_email = emails.get(row.requester_id)
        item.department_name = (
            depts.get(row.department_id) if row.department_id else None
        )
        out.append(item)
    return out


@router.post("", response_model=InviteRequestOut, status_code=status.HTTP_201_CREATED)
async def create_request(
    payload: InviteRequestCreate, user: CurrentUser, _m: MemberUser, db: DB
):
    request = await invite_request_service.create_request(
        db,
        requester_id=user.id,
        role=payload.role,
        count=payload.count,
        reason=payload.reason,
        department_id=str(payload.department_id) if payload.department_id else None,
    )
    await db.commit()
    await db.refresh(request)
    return (await _project(db, [request]))[0]


@router.get("", response_model=InviteRequestList)
async def list_requests(
    user: CurrentUser,
    _m: MemberUser,
    db: DB,
    status_filter: str | None = Query(
        default=None, alias="status", pattern="^(pending|approved|rejected)$"
    ),
):
    is_admin = user.role == "admin"
    rows = await invite_request_service.list_requests(
        db, user_id=user.id, is_admin=is_admin, status=status_filter
    )
    items = await _project(db, rows)
    return {"items": items, "total": len(items)}


@router.post("/{request_id}/approve", response_model=InviteRequestOut)
async def approve_request(request_id: ResourceId, admin: AdminUser, db: DB):
    request, codes = await invite_request_service.approve(
        db, request_id=request_id, admin_id=admin.id
    )
    await write_audit(
        db,
        user_id=admin.id,
        action="invite_request_approve",
        target_type="invite_request",
        target_id=request_id,
        detail={"count": len(codes)},
        strict=True,
    )
    await db.commit()
    await db.refresh(request)
    return (await _project(db, [request]))[0]


@router.post("/{request_id}/reject", response_model=InviteRequestOut)
async def reject_request(
    request_id: ResourceId,
    payload: InviteRequestReject,
    admin: AdminUser,
    db: DB,
):
    request = await invite_request_service.reject(
        db, request_id=request_id, admin_id=admin.id, reason=payload.reason
    )
    await write_audit(
        db,
        user_id=admin.id,
        action="invite_request_reject",
        target_type="invite_request",
        target_id=request_id,
        detail={"reason": payload.reason},
        strict=True,
    )
    await db.commit()
    await db.refresh(request)
    return (await _project(db, [request]))[0]
