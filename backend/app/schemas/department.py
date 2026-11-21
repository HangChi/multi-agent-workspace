"""组织架构 schema（020）。"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class DepartmentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    parent_id: UUID | None = None
    # 分派范围：all（全公司）/ subtree（本部门及下级）/ self（仅本部门）
    assign_scope: Literal["all", "subtree", "self"] = "all"


class DepartmentUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    assign_scope: Literal["all", "subtree", "self"] | None = None


class DepartmentOut(BaseModel):
    model_config = {"from_attributes": True}

    id: str
    name: str
    parent_id: str | None = None
    # 物化路径 /a/b/ ——前端按它排序即可得到树的先序，不需要自己拼递归
    path: str
    depth: int
    assign_scope: str
    member_count: int = 0
    created_at: datetime


class DepartmentList(BaseModel):
    items: list[DepartmentOut]
    total: int


class UserDepartmentUpdate(BaseModel):
    """把某人放进某个部门；null = 移出（未归属）。"""

    department_id: UUID | None = None
