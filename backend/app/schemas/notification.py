"""站内通知 schema（007）：列表项 + 未读数 + 批量已读入参。"""

from datetime import datetime

from pydantic import BaseModel, Field


class NotificationOut(BaseModel):
    model_config = {"from_attributes": True}

    id: str
    type: str
    request_id: str | None = None
    title: str
    payload: dict
    read_at: datetime | None = None
    created_at: datetime


class NotificationList(BaseModel):
    items: list[NotificationOut]
    total: int


class UnreadCountOut(BaseModel):
    unread: int


class MarkReadIn(BaseModel):
    """批量已读：ids 缺省 = 全部已读；显式空列表 = 无操作（返回 0）。"""

    ids: list[str] | None = Field(default=None, max_length=100)
