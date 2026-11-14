"""需求 schema（T043）：RequestCreate/RequestUpdate/RequestOut，priority/status 枚举。

对齐 contracts/api.md 需求部分与 data-model.md requests 表：
- priority: high/normal/low（默认 normal）
- status: created/assigned/running/completed/failed/cancelled（默认 created）
- attachments: 附件元数据数组（key/name/size/mime）
"""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, field_validator


class Priority(str, Enum):
    high = "high"
    normal = "normal"
    low = "low"


class RequestStatus(str, Enum):
    created = "created"
    assigned = "assigned"
    running = "running"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class AttachmentMeta(BaseModel):
    """附件元数据（与 requests.attachments jsonb 元素对应）。"""

    key: str
    name: str
    size: int
    mime: str


class AttachmentUploadOut(BaseModel):
    """附件上传响应：元数据 + 预签名 PUT URL。"""

    key: str
    name: str
    size: int
    mime: str
    presigned_put_url: str


class AttachmentListOut(BaseModel):
    attachments: list[AttachmentUploadOut]


class RequestCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str | None = None
    expected_output: str | None = None
    priority: Priority = Priority.normal

    @field_validator("title")
    @classmethod
    def title_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("title 不能为空")
        return v


class RequestUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    expected_output: str | None = None
    priority: Priority | None = None

    @field_validator("title")
    @classmethod
    def title_not_blank(cls, v: str | None) -> str | None:
        if v is not None:
            v = v.strip()
            if not v:
                raise ValueError("title 不能为空")
        return v


class RequestOut(BaseModel):
    id: str
    owner_id: str
    title: str
    description: str | None = None
    expected_output: str | None = None
    priority: str
    status: str
    attachments: list[dict] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class RequestList(BaseModel):
    items: list[RequestOut]
    total: int
