"""模板 schema 与 Agent schema 的约束一致性（反漂移销）。

005 的模板表与 agents 表有 10 个同义配置字段。若两套 Pydantic schema 各写一份
`Field(...)`，迟早会漂移出一处「模板能存、Agent 不能建」的错配（spec FR-022）。
本文件逐字段比对两边的约束，任何一边被单独放宽都会红。

同时锁住工具目录校验：不在 `CATALOG` 的名字必须在 schema 层被拒（422 路径），
而不是留到服务层。
"""

import pytest
from pydantic import ValidationError

from app.executors.catalog import CATALOG
from app.schemas.agent import AgentCreate, AgentUpdate
from app.schemas.agent_template import AgentTemplateCreate, AgentTemplateUpdate

# 两套 schema 共有的配置字段（模板没有 status，Agent 没有 name 唯一性）。
SHARED_FIELDS = (
    "description",
    "system_prompt",
    "model",
    "temperature",
    "max_steps",
    "max_run_seconds",
    "tools",
    "cost_limit_cents",
    "egress_mode",
)


_NUMERIC = {"Ge": "ge", "Gt": "gt", "Le": "le", "Lt": "lt"}
_LENGTH = {"MinLen": "min_length", "MaxLen": "max_length"}


def _constraints(model, field: str) -> dict:
    """取出字段上声明的约束（metadata 里的数值/长度型约束 + default + required）。"""
    f = model.model_fields[field]
    out: dict = {"default": f.default, "required": f.is_required()}
    for m in f.metadata:
        name = type(m).__name__
        if name in _NUMERIC:
            out[name] = getattr(m, _NUMERIC[name])
        elif name in _LENGTH:
            out[name] = getattr(m, _LENGTH[name])
    return out


@pytest.mark.parametrize("field", SHARED_FIELDS)
def test_template_constraints_match_agent_create(field: str):
    assert _constraints(AgentTemplateCreate, field) == _constraints(
        AgentCreate, field
    ), f"字段 {field} 的约束与 AgentCreate 不一致：模板与 Agent 必须同源"


@pytest.mark.parametrize("field", SHARED_FIELDS)
def test_template_constraints_match_agent_update(field: str):
    assert _constraints(AgentTemplateUpdate, field) == _constraints(AgentUpdate, field)


def test_template_name_constraints_match_agent_name():
    assert _constraints(AgentTemplateCreate, "name") == _constraints(
        AgentCreate, "name"
    )


def test_template_rejects_unknown_tool_at_schema_layer():
    """不在工具目录的名字必须在 schema 层被拒（422 路径），不能漏到服务层。"""
    bogus = "definitely-not-a-tool"
    assert bogus not in CATALOG
    with pytest.raises(ValidationError):
        AgentTemplateCreate(name="t", system_prompt="p", tools=[bogus])
    with pytest.raises(ValidationError):
        AgentTemplateUpdate(tools=[bogus])


def test_template_accepts_every_catalog_tool():
    with_tools = AgentTemplateCreate(name="t", system_prompt="p", tools=sorted(CATALOG))
    assert set(with_tools.tools) == set(CATALOG)


def test_template_defaults_match_agent_defaults():
    a = AgentCreate(name="a", system_prompt="p")
    t = AgentTemplateCreate(name="t", system_prompt="p")
    for field in SHARED_FIELDS:
        assert getattr(t, field) == getattr(a, field), f"默认值不一致: {field}"
