"""模板响应派生字段的单一来源（R5）。

`is_builtin` 与 `visibility` 都是**派生字段**，不落库：
- `is_builtin = owner_id is None`
- 内置模板恒为 `visibility="team"`（023：对所有成员可用）

R5 之前这段推理写在两个地方：`api/v1/agent_templates.py` 的 `_out`，以及
`api/v1/agents.py` 的「另存为模板」端点。后者硬写 `out.is_builtin = False`——
当时成立（另存出来的模板必然有 owner），但那是**推理**而非**声明**：
一旦 `save_as_template` 允许 owner 为空（系统预置 / 迁移导入 / 将来加个
「另存为内置」），那行就会把一个内置模板报成非内置，而「内置 = 只读」
正是 005 FR-015 的判据 —— 前端据此显示编辑按钮，后端据此返回 403，
两边会一起错。

本文件的断言都直接作用在 `to_out` 上：它是这两个派生字段的唯一来源。
"""

from types import SimpleNamespace

from app.schemas.agent_template import AgentTemplateOut
from app.services.agent_template_service import to_out

# `AgentTemplateOut.model_validate` 走 from_attributes，需要这些必填属性。
_REQUIRED = {
    "id": "tpl-1",
    "name": "模板",
    "description": None,
    "system_prompt": "你是助手",
    "model": "openai/gpt-4o-mini",
    "temperature": 0.7,
    "max_steps": 10,
    "max_run_seconds": 1800,
    "tools": [],
    "cost_limit_cents": 10000,
    "egress_mode": "default",
    "visibility": "private",
    "created_at": "2026-01-01T00:00:00Z",
    "updated_at": "2026-01-01T00:00:00Z",
}


def _template(**overrides):
    return SimpleNamespace(**{**_REQUIRED, **overrides})


def test_builtin_is_derived_from_missing_owner() -> None:
    """owner_id 为空 → 内置。不读任何 `is_builtin` 列（那列不存在）。"""
    out = to_out(_template(owner_id=None))
    assert out.is_builtin is True
    assert isinstance(out, AgentTemplateOut)


def test_owned_template_is_not_builtin() -> None:
    assert to_out(_template(owner_id="user-1")).is_builtin is False


def test_builtin_visibility_is_coerced_to_team() -> None:
    """内置模板即使库里 visibility='private'，对外也必须是 team。

    内置模板由种子脚本注入，`visibility` 列走默认值 'private'。如果照搬这个
    列值，前端会把「全员可用」的内置模板标成私有，用户看不到它。
    """
    out = to_out(_template(owner_id=None, visibility="private"))
    assert out.visibility == "team"


def test_owned_template_keeps_stored_visibility() -> None:
    """有 owner 的模板不强制改写 visibility：team/private 都由用户自己设。"""
    for visibility in ("private", "team"):
        out = to_out(_template(owner_id="user-1", visibility=visibility))
        assert out.visibility == visibility


def test_to_out_does_not_mutate_the_row() -> None:
    """派生只影响响应对象，不能回写 ORM 行（否则 flush 会把 team 写进库里）。"""
    row = _template(owner_id=None, visibility="private")
    to_out(row)
    assert row.visibility == "private"
