"""项目 schema（008）：CRUD 出入参 + 计数展示字段。

ProjectOut 带 `request_count`——筛选下拉要「每个项目几件活」，
前端逐个项目发一次 /requests 数数是 N+1（FR-010 的后端兑现）。
"""

from datetime import datetime

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
    created_at: datetime
    updated_at: datetime


class ProjectList(BaseModel):
    items: list[ProjectOut]
    total: int
