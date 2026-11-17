"""Agent 相关 Pydantic schema（Phase 3 骨架 + Phase 4 完整）。

对齐 contracts/api.md Agent 部分。
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.executors.catalog import CATALOG

# 工具白名单**从工具目录派生**，不再手写一份。
# 001 时代这里是硬编码的 5 个旧名字（web_fetch/shell/http/code_interpreter），
# 与执行器实际实现漂移——catalog 才是单一事实源。
ALLOWED_TOOLS = frozenset(CATALOG)


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
    # 出站模式：default 只受全局黑名单约束；restricted 只放行 agent 级 allow 规则。
    # 无论哪种，全局黑名单都不可放宽（FR-022）。
    egress_mode: Literal["default", "restricted"] = "default"

    @field_validator("tools")
    @classmethod
    def _check_tools(cls, v: list[str]) -> list[str]:
        bad = [t for t in v if t not in ALLOWED_TOOLS]
        if bad:
            raise ValueError(f"工具不在白名单: {bad}（允许: {sorted(ALLOWED_TOOLS)}）")
        return v


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
    egress_mode: Literal["default", "restricted"] | None = None

    @field_validator("tools")
    @classmethod
    def _check_tools(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return v
        bad = [t for t in v if t not in ALLOWED_TOOLS]
        if bad:
            raise ValueError(f"工具不在白名单: {bad}（允许: {sorted(ALLOWED_TOOLS)}）")
        return v


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
    egress_mode: str
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class AgentSaveOut(AgentOut):
    """Agent 保存响应；warning 不阻止保存。"""

    warning: str | None = None


class AgentList(BaseModel):
    """Agent 列表响应。"""

    items: list[AgentOut]
    total: int
