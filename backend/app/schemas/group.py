"""Agent 组相关 Pydantic schema（T032）。

对齐 contracts/api.md Agent 组部分：
- GroupCreate/GroupUpdate：name/description/routing_strategy（枚举校验）
- GroupMemberOut：组内成员（含 Agent 名快照）
- GroupWithMembers：组 + 有序成员
"""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class RoutingStrategy(str, Enum):
    """路由策略：direct/fan_out 已实现；pipeline/router 保留入口（Phase 6 展开）。"""

    DIRECT = "direct"
    FAN_OUT = "fan_out"
    PIPELINE = "pipeline"
    ROUTER = "router"


class GroupCreate(BaseModel):
    """创建组请求。"""

    name: str = Field(min_length=1, max_length=100)
    description: str | None = None
    routing_strategy: RoutingStrategy = RoutingStrategy.FAN_OUT


class GroupUpdate(BaseModel):
    """编辑组请求（部分字段）。"""

    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = None
    routing_strategy: RoutingStrategy | None = None


class GroupMemberAdd(BaseModel):
    """添加成员请求。"""

    agent_id: str = Field(min_length=1)
    position: int = Field(ge=1)


class GroupMemberReorder(BaseModel):
    """成员排序请求：positions 数组整体替换排序。"""

    positions: list["GroupPosition"]


class GroupPosition(BaseModel):
    agent_id: str
    position: int = Field(ge=1)


class GroupMemberOut(BaseModel):
    """组内成员（含 Agent 名快照，供前端展示）。"""

    agent_id: str
    name: str
    position: int

    model_config = {"from_attributes": True}


class GroupOut(BaseModel):
    """组公开信息（不含成员展开）。"""

    id: str
    owner_id: str
    name: str
    description: str | None = None
    routing_strategy: str
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class GroupWithMembers(GroupOut):
    """组 + 有序成员。"""

    members: list[GroupMemberOut] = Field(default_factory=list)


class GroupList(BaseModel):
    """组列表响应。"""

    items: list[GroupWithMembers]
    total: int
