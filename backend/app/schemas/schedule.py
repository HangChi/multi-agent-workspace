"""定时调度 schema（T013）。

对齐 contracts/api.md 的三份契约与 data-model.md 的两张表：

- 规则管理：`ScheduleCreate` / `ScheduleUpdate`（部分更新）/ `ScheduleOut`
- 触发历史：`TriggerOut` / `TriggerListOut`（含全量 `counts`）
- 内部契约不对外暴露 schema，只落库

一处刻意的设计：`template` 与 `target` 各自**嵌套**成对象，而不是平铺在
`ScheduleCreate` 上。原因是 PATCH 的语义——`null` 表示「不改」，
而「把 template.description 清空」是空字符串。若把四五个模板字段平铺，
PATCH 时「没传这个字段」与「传了 null」在 Pydantic 里无法区分（都有
`exclude_unset` 的解法，但平铺后每加一个模板字段就要多一次判断）。
嵌套成对象后，`template=null` 与 `template={...}` 天然二义，
`template` 内部再用 `exclude_unset` 表达「只改其中一项」。
"""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, field_validator, model_validator

from app.schemas.request import Priority


class ScheduleTargetType(str, Enum):
    """执行目标类型。多态二选一，无法用单个外键表达（data-model 已说明）。"""

    agent = "agent"
    group = "group"


class ScheduleTriggerResult(str, Enum):
    """触发结果四态。

    `missed` 与 `skipped_busy` 必须分开：前者是「服务没在跑」，
    后者是「上一次还没跑完」。两者的处置完全相反——前者不重试，
    后者说明系统过载或任务超时，需要人去看任务本身。
    """

    fired = "fired"
    missed = "missed"
    skipped_busy = "skipped_busy"
    failed = "failed"


class TargetStatus(str, Enum):
    """目标在展示时的状态。

    `missing` 是刻意加的第五种：归档（`archived`）是软删，可以恢复，
    而目标记录已被物理删除时无法恢复。两者对用户的含义不同，
    合并成 null 会让界面无法提示「这个规则的目标已经不存在了，请重建」。
    """

    active = "active"
    archived = "archived"
    missing = "missing"


class ScheduleTemplate(BaseModel):
    """建单模板。只含现有 `RequestCreate` 能接受的四项。

    实测 `requests` 表没有 `deadline` 列，`RequestCreate` 也不接受附件，
    因此模板不含这两项（见 data-model.md「不做哪些变更」）。
    """

    title: str = Field(min_length=1, max_length=200)
    description: str | None = None
    expected_output: str | None = None
    priority: Priority = Priority.normal

    @field_validator("title")
    @classmethod
    def title_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("模板标题不能为空")
        return v


class ScheduleTarget(BaseModel):
    """执行目标。

    `strategy` 只对 `group` 有意义；对 `agent` 传非空值是契约第 6 步的
    400（不是 422）——因为这是「两个合法值之间的相容性」问题，
    不是「字段本身不合法」。
    """

    type: ScheduleTargetType
    id: str = Field(min_length=1)
    strategy: str | None = Field(
        default=None, pattern="^(direct|fan_out|pipeline|router)$"
    )


class ScheduleCreate(BaseModel):
    """创建规则。

    注意这里**不做** cron 与时区的语义校验——那是 service 层第 3/4 步的事，
    顺序本身就是契约（形状 422 必须早于 cron 422）。schema 只保证形状。
    """

    name: str = Field(min_length=1, max_length=100)
    description: str | None = None
    cron_expr: str = Field(min_length=1, max_length=120)
    timezone: str = Field(default="Asia/Shanghai", min_length=1, max_length=64)
    template: ScheduleTemplate
    target: ScheduleTarget
    enabled: bool = True

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("规则名称不能为空")
        return v

    @field_validator("cron_expr")
    @classmethod
    def cron_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("周期表达式不能为空")
        return v


class ScheduleTemplatePatch(BaseModel):
    """模板的部分更新。

    `description` / `expected_output` 用空字符串表示「清空」，
    与契约「null 表示不改」一致；`title` 不允许清空（min_length=1）。
    """

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
                raise ValueError("模板标题不能为空")
        return v


class ScheduleUpdate(BaseModel):
    """部分更新。

    **所有字段默认 None 表示「不改」**，而不是清空。要清空
    `description` 用空字符串（C 端约定，见契约 §1 PATCH）。
    """

    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = None
    cron_expr: str | None = Field(default=None, min_length=1, max_length=120)
    timezone: str | None = Field(default=None, min_length=1, max_length=64)
    template: ScheduleTemplatePatch | None = None
    target: ScheduleTarget | None = None
    enabled: bool | None = None

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, v: str | None) -> str | None:
        if v is not None:
            v = v.strip()
            if not v:
                raise ValueError("规则名称不能为空")
        return v

    @field_validator("cron_expr")
    @classmethod
    def cron_not_blank(cls, v: str | None) -> str | None:
        if v is not None:
            v = v.strip()
            if not v:
                raise ValueError("周期表达式不能为空")
        return v

    def changes(self) -> dict:
        """返回用户**真正传了**的字段。

        必须用 `exclude_unset=True`：不带它时 `model_dump()` 会把没传的字段
        也补成默认值输出，PATCH 就变成「把用户没提到的字段全部清空」。
        这个 bug 只在部分更新时显现，接口测试若只测全量更新就完全测不到。

        封装成方法而不是让调用方各自记得传参数：这类陷阱靠约定守不住，
        靠「没有别的写法可用」才守得住。
        """
        return self.model_dump(exclude_unset=True)


class ScheduleTargetOut(ScheduleTarget):
    """输出用目标：多两个展示字段。

    `name` / `status` 每次读取实时取、不缓存，让界面能直接显示
    「目标已归档」而不必再发一次请求。目标被删除时 `name` 为 null、
    `status` 为 `missing`。
    """

    name: str | None = None
    status: TargetStatus = TargetStatus.active


class ScheduleOut(BaseModel):
    """规则完整输出。"""

    id: str
    owner_id: str
    # 仅 admin 视角填充；member 看自己时为 null（契约 §1 响应体注释）
    owner_email: str | None = None
    name: str
    description: str | None = None
    cron_expr: str
    timezone: str
    template: ScheduleTemplate
    target: ScheduleTargetOut
    enabled: bool
    next_run_at: datetime | None = None
    last_run_at: datetime | None = None
    last_result: ScheduleTriggerResult | None = None
    consecutive_failures: int = 0
    auto_disabled_reason: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ScheduleList(BaseModel):
    items: list[ScheduleOut]
    total: int


class TriggerOut(BaseModel):
    """一条触发事实。

    `fired_at` 可为 null：错过记录只是「被告知没跑」，没有实际执行时刻。
    `missed_count` 只在 `result=missed` 时 > 0，表示这条记录合并了几次错过。
    """

    id: str
    schedule_id: str
    planned_at: datetime
    fired_at: datetime | None = None
    result: ScheduleTriggerResult
    reason: str | None = None
    missed_count: int = 0
    window_start: datetime | None = None
    window_end: datetime | None = None
    request_id: str | None = None
    run_id: str | None = None

    model_config = {"from_attributes": True}


class TriggerCounts(BaseModel):
    """全量结果分布（非当前页、非当前过滤）。

    四个字段都给默认值 0：某结果一次都没出现时也要出现在响应里，
    否则前端得为「字段可能不存在」写分支。
    """

    fired: int = 0
    missed: int = 0
    skipped_busy: int = 0
    failed: int = 0


class TriggerListOut(BaseModel):
    items: list[TriggerOut]
    total: int
    counts: TriggerCounts = Field(default_factory=TriggerCounts)


class CronPreviewIn(BaseModel):
    """预览下次触发时间（前端建规则时实时提示用）。

    不落库、不需要规则存在，因此独立于 CRUD。
    """

    cron_expr: str = Field(min_length=1, max_length=120)
    timezone: str = Field(default="Asia/Shanghai", min_length=1, max_length=64)
    # 预览几次，默认 3：够看出「是不是每天/每周」，又不至于刷屏
    count: int = Field(default=3, ge=1, le=10)


class CronPreviewOut(BaseModel):
    """预览结果。

    解析失败时 `error` 非空、`next_runs` 为空列表——用 200 返回而不是 4xx，
    因为这是「边输边提示」的场景，输入过程中的不完整表达式不是错误请求。
    """

    next_runs: list[datetime] = Field(default_factory=list)
    error: str | None = None

    @model_validator(mode="after")
    def _never_silently_empty(self) -> "CronPreviewOut":
        # 能解析却算不出任何下次时间，本质是「永远不触发」。
        # 不能返回「成功 + 空列表」——界面会显示成「暂无下次」，
        # 而配置看起来完全正常，用户无从知道它永远不会跑。
        # 这里补一条提示而不是抛异常：输出模型抛异常在线上等于 500，
        # 而这本来就是一个应当被友好告知的输入问题。
        if self.error is None and not self.next_runs:
            self.error = "无法计算下次触发时间，请检查周期表达式"
        return self
