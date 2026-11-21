"""项目 schema（008）：CRUD 出入参 + 计数展示字段。

ProjectOut 带 `request_count`——筛选下拉要「每个项目几件活」，
前端逐个项目发一次 /requests 数数是 N+1（FR-010 的后端兑现）；
010 再加 `member_count`（含 owner）。
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


def _strip_name(value: str | None) -> str | None:
    """项目名去空白；去完为空视为未填（min_length 只数字符数，
    三个空格也是 length=3——必须显式拦，同 007 RejectIn.reason 的纪律）。"""
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        raise ValueError("项目名不能为空白")
    return stripped


class ProjectCreate(BaseModel):
    # 1..100 与 Agent/组名同纪律；去空白后非空
    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=2000)

    _normalize_name = field_validator("name")(_strip_name)


class ProjectUpdate(BaseModel):
    """全可选。status 仅 active/archived（service 再兜一层语义校验）。"""

    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=2000)
    status: str | None = Field(default=None, pattern="^(active|archived)$")

    _normalize_name = field_validator("name")(_strip_name)


class ProjectOut(BaseModel):
    model_config = {"from_attributes": True}

    id: str
    owner_id: str
    name: str
    description: str | None = None
    status: str
    # 该项目下、当前调用人可见范围内的需求数（我创建的 + 分派给我的）
    request_count: int = 0
    # 010：成员数（含 owner——「这个项目有几个人」的自然口径，FR-009）
    member_count: int = 1
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(
        cls,
        project: object,
        *,
        request_count: int = 0,
        member_count: int = 1,
    ) -> "ProjectOut":
        """由 ORM 行 + 两个计数字段构造响应（R5）。

        此前 4 个端点各写一遍
        `ProjectOut.model_validate(p).model_copy(update={...})`，计数字段的
        默认值靠人记：新建项目传 `request_count=0`，列表传 `member_rows + 1`
        （+1 是 owner），详情/改/移交传 service 算好的值。哪里忘了带
        `member_count` 就用默认值 1 —— 而 1 恰好是「只有 owner」这个合法值，
        错了也看不出来。

        收敛成一处后，「含 owner 的 +1」只在 `project_member_service.member_count`
        与这里两处出现，且后者由前者喂值。
        """
        return cls.model_validate(project).model_copy(
            update={"request_count": request_count, "member_count": member_count}
        )


class ProjectList(BaseModel):
    items: list[ProjectOut]
    total: int


class ProjectTransferIn(BaseModel):
    """移交给谁（017）。

    为什么不是一个字段更新：移交要同时改归属、增删成员行、发通知、写独立审计，
    且 409 有四种语义（自移交/非成员/viewer/同名）——塞进 PATCH 会让
    前端拿到 409 无法分辨该提示什么（research R-01）。
    """

    new_owner_id: UUID
    # 默认保留访问：交接不等于立刻断联。离职场景显式传 False（R-04）。
    keep_as_member: bool = True
