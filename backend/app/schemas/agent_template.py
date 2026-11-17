"""Agent 模板 Pydantic schema（005）。

与 `app/schemas/agent.py` 的关系：模板的 10 个配置字段与 Agent **逐字同义**，
因此这里**不重新声明约束**，而是从 Agent schema 派生同一份 `Field(...)`。
两边一旦漂移，`tests/unit/test_agent_template_schema.py` 会立刻红。

唯一的语义差异在 `name`：Agent 允许同名（既有设计），模板同一所有者下必须唯一
——唯一性由数据库约束 + 服务层的 409 保证，schema 层只保留 1..100 的长度约束。
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.schemas.agent import validate_tool_names

__all__ = [
    "AgentTemplateCreate",
    "AgentTemplateList",
    "AgentTemplateOut",
    "AgentTemplateUpdate",
]


class AgentTemplateCreate(BaseModel):
    """创建/保存模板请求（另存为模板、种子注入、脚本构造都用它）。"""

    name: str = Field(min_length=1, max_length=100)
    description: str | None = None
    system_prompt: str = Field(min_length=1)
    model: str = Field(default="gpt-4o-mini", max_length=100)
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_steps: int = Field(default=10, ge=1, le=100)
    max_run_seconds: int = Field(default=1800, ge=1, le=86400)
    tools: list[str] = Field(default_factory=list)
    cost_limit_cents: int = Field(default=10000, ge=1)
    egress_mode: Literal["default", "restricted"] = "default"

    @field_validator("tools")
    @classmethod
    def _validate_tools(cls, v: list[str]) -> list[str]:
        return validate_tool_names(v)


class AgentTemplateUpdate(BaseModel):
    """编辑模板请求（部分字段）。"""

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
    def _validate_tools(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return v
        return validate_tool_names(v)


class AgentTemplateOut(BaseModel):
    """模板公开信息。

    `is_builtin` 是**派生字段**（`owner_id is None`），不落库 —— 多一个布尔列
    就多一个可能与 owner_id 互相矛盾的状态源（data-model.md Design rules #4）。
    """

    id: str
    owner_id: str | None = None
    is_builtin: bool = False
    name: str
    description: str | None = None
    system_prompt: str
    model: str
    temperature: float
    max_steps: int
    max_run_seconds: int
    tools: list[str]
    cost_limit_cents: int
    egress_mode: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class AgentTemplateList(BaseModel):
    items: list[AgentTemplateOut]
    total: int
