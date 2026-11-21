"""外部通知通道的出入参（018）。

secret **不进任何出参**：列表里只给 `secret_configured`。重置是「生成新的」，
不提供查看——能看就能被中间层日志顺手抄走（research R-08）。
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class NotificationChannelCreate(BaseModel):
    # 本期只有 webhook；写成 Literal 让传 email 的人立刻拿到 422，
    # 而不是一个「保存成功但永远不投递」的幽灵配置。
    kind: Literal["webhook"] = "webhook"
    url: str = Field(min_length=8, max_length=2048)
    # 空列表 = 订阅全部事件
    event_types: list[str] = Field(default_factory=list, max_length=50)
    enabled: bool = True


class NotificationChannelUpdate(BaseModel):
    url: str | None = Field(default=None, min_length=8, max_length=2048)
    event_types: list[str] | None = Field(default=None, max_length=50)
    enabled: bool | None = None


class NotificationChannelOut(BaseModel):
    model_config = {"from_attributes": True}

    id: str
    kind: str
    url: str
    event_types: list[str]
    enabled: bool
    created_at: datetime
    updated_at: datetime
    # 固定 True：secret 一定存在（服务端生成），但从不回显它的值
    secret_configured: bool = True


class NotificationChannelList(BaseModel):
    items: list[NotificationChannelOut]
    total: int


class NotificationDeliveryOut(BaseModel):
    model_config = {"from_attributes": True}

    id: str
    channel_id: str
    notification_id: str | None = None
    event_type: str
    status: str
    attempts: int
    last_error: str | None = None
    last_attempt_at: datetime | None = None
    created_at: datetime


class NotificationDeliveryList(BaseModel):
    items: list[NotificationDeliveryOut]
    total: int


class ChannelTestResult(BaseModel):
    """`POST /{id}/test` 的同步结果（不落投递记录，FR-009）。"""

    ok: bool
    status_code: int | None = None
    error: str | None = None


class EventTypeOut(BaseModel):
    """可订阅事件（前端渲染 checkbox 用，标签由后端给，避免两端各写一份中文）。"""

    value: str
    label: str


class EventTypeList(BaseModel):
    items: list[EventTypeOut]
