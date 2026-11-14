"""Agent 相关 Pydantic schema（Phase 3 骨架 + Phase 4 完整）。

对齐 contracts/api.md Agent 部分。
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class AgentCreate(BaseModel):
    """创建 Agent 请求。"""

    name: str = Field(min_length=1, max_length=100)
    description: str | None = None
    system_prompt: str = Field(min_length=1)
    model: str = Field(default="gpt-4o-mini", max_length=100)
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_steps: int = Field(default=10, ge=1, le=100)
    max_run_seconds: int = Field(default=1800, ge=1, le=86400)
    tools: list[str] = Field(default_factory=list)
    cost_limit_cents: int = Field(default=10000, ge=1)


class AgentUpdate(BaseModel):
    """编辑 Agent 请求（部分字段）。"""

    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = None
    system_prompt: str | None = Field(default=None, min_length=1)
    model: str | None = Field(default=None, max_length=100)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    max_steps: int | None = Field(default=None, ge=1, le=100)
    max_run_seconds: int | None = Field(default=None, ge=1, le=86400)
    tools: list[str] | None = None
    cost_limit_cents: int | None = Field(default=None, ge=1)


class AgentOut(BaseModel):
    """Agent 公开信息。"""

    id: str
    owner_id: str
    name: str
    description: str | None = None
    system_prompt: str
    model: str
    temperature: float
    max_steps: int
    max_run_seconds: int
    tools: list[Any]
    cost_limit_cents: int
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class AgentList(BaseModel):
    """Agent 列表响应。"""

    items: list[AgentOut]
    total: int
