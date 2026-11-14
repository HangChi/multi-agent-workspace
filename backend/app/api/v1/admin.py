"""管理后台端点（admin-only，含审计日志查询）。

出站黑名单与访问查询位于 admin_egress.py，统一由 /admin 前缀提供。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select

from app.api.deps import DB, CurrentUser, require_role
from app.models.audit_log import AuditLog
from app.schemas.common import AuditLogList, AuditLogOut

router = APIRouter(prefix="/audit-logs", tags=["admin"])

AdminUser = Annotated[object, Depends(require_role("admin"))]


@router.get("", response_model=AuditLogList)
async def list_audit_logs(
    _user: CurrentUser,
    _admin: AdminUser,
    db: DB,
    user_id: str | None = Query(default=None),
    action: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
):
    filters = []
    if user_id:
        filters.append(AuditLog.user_id == user_id)
    if action:
        filters.append(AuditLog.action == action)
    total = int((await db.execute(select(func.count()).select_from(AuditLog).where(*filters))).scalar_one())
    rows = (
        await db.execute(
            select(AuditLog).where(*filters).order_by(AuditLog.created_at.desc())
            .offset((page - 1) * page_size).limit(page_size)
        )
    ).scalars().all()
    return {"items": [AuditLogOut.model_validate(row) for row in rows], "total": total}
