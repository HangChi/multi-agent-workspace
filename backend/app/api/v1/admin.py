"""管理后台端点（admin-only，T020 权限验证载体 + T079 审计查询）。

Phase 3：GET /audit-logs（admin 专属）——作为权限验证载体。
Phase 8（T079）：补全筛选（user_id / action）与分页（page / page_size）。
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
    user_id: str | None = Query(default=None, description="按用户筛选"),
    action: str | None = Query(default=None, description="按动作筛选"),
    page: int = Query(default=1, ge=1, description="页码，从 1 开始"),
    page_size: int = Query(default=50, ge=1, le=100, description="每页条数"),
):
    """审计日志列表（仅 admin，member/viewer 403）。

    支持：
    - ?user_id=<uuid>  只返回该用户的日志
    - ?action=<str>    只返回该动作的日志
    - ?page=&page_size= 分页（默认每页 50）
    - 排序：created_at 降序（最新在前）
    """
    filters = []
    if user_id:
        filters.append(AuditLog.user_id == user_id)
    if action:
        filters.append(AuditLog.action == action)

    total = (
        await db.execute(select(func.count()).select_from(AuditLog).where(*filters))
    ).scalar_one()

    offset = (page - 1) * page_size
    rows = (
        await db.execute(
            select(AuditLog)
            .where(*filters)
            .order_by(AuditLog.created_at.desc())
            .offset(offset)
            .limit(page_size)
        )
    ).scalars().all()
    return {"items": [AuditLogOut.model_validate(r) for r in rows], "total": total}
