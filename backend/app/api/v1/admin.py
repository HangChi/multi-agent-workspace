"""管理后台端点（admin-only，T020 权限验证载体 + T077+ 完整审计）。

Phase 3：GET /audit-logs（admin 专属）——作为权限验证载体。
Phase 8：补全筛选参数与统计。
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import func, select

from app.api.deps import DB, CurrentUser, require_role
from app.models.audit_log import AuditLog
from app.schemas.common import AuditLogList, AuditLogOut

router = APIRouter(prefix="/audit-logs", tags=["admin"])

AdminUser = Annotated[object, Depends(require_role("admin"))]


@router.get("", response_model=AuditLogList)
async def list_audit_logs(
    _user: CurrentUser, _admin: AdminUser, db: DB
):
    """审计日志列表（仅 admin，member/viewer 403）。"""
    total = (await db.execute(select(func.count()).select_from(AuditLog))).scalar_one()
    rows = (
        await db.execute(
            select(AuditLog).order_by(AuditLog.created_at.desc()).limit(50)
        )
    ).scalars().all()
    return {"items": [AuditLogOut.model_validate(r) for r in rows], "total": total}
