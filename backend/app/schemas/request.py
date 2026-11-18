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
    pending = "pending"
    accepted = "accepted"
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
    """附件上传凭据。

    用 **POST Policy**（表单上传）而不是预签名 PUT：只有 policy 的
    `content-length-range` 能让对象存储在**服务端强制**大小上限。
    预签名 PUT 无法附带任何长度条件 —— 客户端声明 size=1 再 PUT 一个 1GB
    文件是拦不住的（这正是修复前的漏洞）。
    """

    key: str
    name: str
    size: int
    mime: str
    #: 表单 POST 的目标地址：`{minio}/{bucket}`
    upload_url: str
    #: POST 时需要原样带回的表单字段（含 policy 签名）
    upload_fields: dict[str, str]


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
    # 来源调度（004）：手工创建的需求为 null，调度产生的需求指向其规则。
    # 两个字段都可空且默认 null —— 001 的既有契约断言不受影响。
    source_schedule_id: str | None = None
    #: 展示用：来源规则名。规则已删除时为 null（外键 SET NULL，历史需求保留）
    source_schedule_name: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class RequestList(BaseModel):
    items: list[RequestOut]
    total: int


class AssigneeType(str, Enum):
    agent = "agent"
    group = "group"
    # 007：人作为落点 = 转交处理责任（不建 Run、不入队）。
    # 与 agent/group 并列同一个 assign 入口，前端拖拽目标注册表不分叉。
    user = "user"


class AssignRequest(BaseModel):
    """分配请求（T055 + 007）：assignee_type + assignee_id + 可选 strategy。

    `note`：分派/转交留言（仅 user 落点有意义，agent/group 分支忽略），
    随历史与通知 payload 落库。
    """

    assignee_type: AssigneeType
    assignee_id: str = Field(min_length=1)
    strategy: str | None = Field(
        default=None, pattern="^(direct|fan_out|pipeline|router)$"
    )
    note: str | None = Field(default=None, max_length=2000)


class RejectIn(BaseModel):
    """退回请求（007）：原因必填——「已退回却查不出理由」的记录没有审计价值。"""

    reason: str = Field(min_length=1, max_length=2000)

    @field_validator("reason")
    @classmethod
    def reason_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("退回原因不能为空")
        return v


class DispatchActionOut(BaseModel):
    """分派类动作响应（202）：需求 id + 迁移后状态。

    刻意不返回完整 RequestOut：accept/reject 的调用方（看板/详情）会
    自行 invalidate 查询，且 3a 尚未扩展 RequestOut 的分派字段（3b 统一加）。
    """

    request_id: str
    status: str


class AssignOut(BaseModel):
    """分配响应（202）：父 Run id + 受理状态。"""

    run_id: str
    status: str = "queued"
