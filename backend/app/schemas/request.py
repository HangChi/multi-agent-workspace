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


class RunOutcomeSummary(BaseModel):
    """需求名下 Run 的结果构成。

    fan_out 的「部分成功」在需求状态上只能显示成 failed（不能静默当完成），
    这个计数让界面能说清「4 个里 3 个完成、1 个失败」，而不是只给一个红标。
    """

    total: int = 0
    completed: int = 0
    failed: int = 0
    cancelled: int = 0
    active: int = 0


class RequestOut(BaseModel):
    id: str
    owner_id: str
    title: str
    description: str | None = None
    expected_output: str | None = None
    priority: str
    status: str
    attachments: list[dict] = Field(default_factory=list)
    # 尚未分配时没有 Run，保持 None（界面据此隐藏「运行结果」）
    runs_summary: RunOutcomeSummary | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class RequestList(BaseModel):
    items: list[RequestOut]
    total: int


class AssigneeType(str, Enum):
    agent = "agent"
    group = "group"


class AssignRequest(BaseModel):
    """分配请求（T055）：assignee_type + assignee_id + 可选 strategy。"""

    assignee_type: AssigneeType
    assignee_id: str = Field(min_length=1)
    strategy: str | None = Field(
        default=None, pattern="^(direct|fan_out|pipeline|router)$"
    )


class AssignOut(BaseModel):
    """分配响应（202）：父 Run id + 受理状态。"""

    run_id: str
    status: str = "queued"
