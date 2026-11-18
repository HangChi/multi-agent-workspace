"""项目成员 schema（010）：出入参。

Out 带 `user_email`（列表直接展示，不 N+1 查用户）与 `is_owner`
（owner 也在列表里，用标记区分——成员表不存 owner 行，R-01）。
"""

import uuid

from pydantic import BaseModel


class ProjectMemberCreate(BaseModel):
    # 目标用户。必须是活跃用户（停用 400，R-08）。
    user_id: uuid.UUID


class ProjectMemberOut(BaseModel):
    model_config = {"from_attributes": True}

    user_id: str
    user_email: str | None = None
    # True = 项目创建者（不在 project_members 表里，列表合成时补上）
    is_owner: bool = False
    joined_at: str


class ProjectMemberList(BaseModel):
    items: list[ProjectMemberOut]
    total: int
