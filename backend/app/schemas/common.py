"""通用 schema：分页与审计输出。"""

from datetime import datetime

from pydantic import BaseModel


class AuditLogOut(BaseModel):
    """审计日志条目。"""

    id: str
    user_id: str
    action: str
    target_type: str | None = None
    target_id: str | None = None
    detail: dict | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class AuditLogList(BaseModel):
    """审计日志列表。"""

    items: list[AuditLogOut]
    total: int
