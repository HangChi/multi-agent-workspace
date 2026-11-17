"""`instantiate()` 与 `clone_agent()` 的纯逻辑单测（T020/T021）。

这两个函数是 005 的核心决策落点，用**假对象**（SimpleNamespace）驱动，
不碰数据库：

- 实例化：模板值打底 → 请求体覆盖；失效工具被过滤并原样返回名字；
- 克隆：10 个字段逐字拷贝；名称派生 `副本` / `副本2` …；超长基名截断；
- restricted 无 allow 规则时给出与 `POST /agents` 逐字相同的警告；
- 两者都**不提交**（service 只 flush，commit 在端点层）。

关于「8 个字段」与「10 个字段」：spec SC-002 说 8 个配置字段一致，
另 2 个是 `name`（按规则派生）与 `egress_mode`（同属配置，一并拷贝）。
这里断言全部 10 个非 name 字段，比 SC-002 更严。
"""

from types import SimpleNamespace

import pytest

from app.api.errors import AppError
from app.services.agent_template_service import (
    SNAPSHOT_FIELDS,
    clone_agent,
    instantiate,
)

# 与 agent_template_service.SNAPSHOT_FIELDS 对应的模板假对象构造
_TEMPLATE_KWARGS = {
    "description": "tpl-desc",
    "system_prompt": "tpl-prompt",
    "model": "tpl-model",
    "temperature": 0.4,
    "max_steps": 5,
    "max_run_seconds": 111,
    "tools": ["calculate", "read_url"],
    "cost_limit_cents": 222,
    "egress_mode": "default",
}


def _template(**overrides):
    base = dict(_TEMPLATE_KWARGS)
    base.update(overrides)
    return SimpleNamespace(**base)


def _agent(**overrides):
    base = {
        "owner_id": "owner-1",
        "name": "源 Agent",
        "description": "agent-desc",
        "system_prompt": "agent-prompt",
        "model": "agent-model",
        "temperature": 0.9,
        "max_steps": 3,
        "max_run_seconds": 999,
        "tools": ["run_python"],
        "cost_limit_cents": 777,
        "egress_mode": "restricted",
        "status": "active",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


# ===== instantiate =====


def test_instantiate_uses_template_values_when_payload_is_empty():
    tpl = _template()
    draft, filtered = instantiate(
        tpl, payload=None, enabled_tools=["calculate", "read_url"]
    )
    assert filtered == []
    assert draft.system_prompt == "tpl-prompt"
    assert draft.model == "tpl-model"
    assert draft.temperature == 0.4
    assert draft.tools == ["calculate", "read_url"]
    assert draft.cost_limit_cents == 222
    assert draft.egress_mode == "default"
    # 模板没有 name，调用方必须显式给
    assert draft.name is None


def test_instantiate_payload_overrides_template_values():
    tpl = _template()
    draft, filtered = instantiate(
        tpl,
        payload={"temperature": 1.5, "tools": ["calculate"], "name": "我的 Agent"},
        enabled_tools=["calculate"],
    )
    assert filtered == []
    assert draft.name == "我的 Agent"
    assert draft.temperature == 1.5
    assert draft.tools == ["calculate"]
    # 未覆盖的字段仍取模板
    assert draft.system_prompt == "tpl-prompt"
    assert draft.model == "tpl-model"


def test_instantiate_filters_disabled_tools_and_reports_them():
    """模板引用已下线工具 → 过滤 + 返回被过滤的名字（FR-008）。"""
    tpl = _template(tools=["calculate", "legacy_shell", "run_python"])
    draft, filtered = instantiate(tpl, payload=None, enabled_tools=["calculate"])
    assert filtered == ["legacy_shell", "run_python"]
    assert draft.tools == ["calculate"]


def test_instantiate_keeps_tool_order_and_deduplicates():
    tpl = _template(tools=["read_url", "calculate", "read_url"])
    draft, filtered = instantiate(
        tpl, payload=None, enabled_tools=["read_url", "calculate"]
    )
    assert filtered == []
    assert draft.tools == ["read_url", "calculate"]


def test_instantiate_all_tools_offline_still_produces_a_draft():
    """全部下线也不能让创建失败——只是工具集为空（FR-008 的边界）。"""
    tpl = _template(tools=["calculate", "read_url"])
    draft, filtered = instantiate(tpl, payload=None, enabled_tools=[])
    assert filtered == ["calculate", "read_url"]
    assert draft.tools == []


def test_instantiate_does_not_mutate_the_template():
    tpl = _template()
    instantiate(tpl, payload={"temperature": 2.0}, enabled_tools=["calculate"])
    assert tpl.temperature == 0.4


def test_instantiate_covers_every_snapshot_field():
    """模板的每个配置字段都必须能被拷贝过去——漏一个就是静默丢配置。"""
    tpl = _template()
    draft, _ = instantiate(tpl, payload=None, enabled_tools=tpl.tools)
    for field in SNAPSHOT_FIELDS:
        if field == "description":
            continue  # description 允许 payload 覆盖为 None，单独验
        assert getattr(draft, field) == getattr(tpl, field), field


def test_instantiate_none_is_treated_as_not_provided():
    """null 一律当「没填」：否则一个没碰描述的用户会静默抹掉模板的适用场景。"""
    tpl = _template(description="tpl-desc")
    draft, _ = instantiate(
        tpl,
        payload={"description": None, "temperature": None},
        enabled_tools=tpl.tools,
    )
    assert draft.description == "tpl-desc"
    assert draft.temperature == 0.4


# ===== clone_agent =====


def test_clone_copies_all_config_fields():
    source = _agent()
    clone = clone_agent(source, owner_id="owner-1", used_names=set())
    for field in SNAPSHOT_FIELDS:
        assert getattr(clone, field) == getattr(source, field), field
    # 实例级字段不属于快照
    assert not hasattr(clone, "id") or clone.id is None
    assert clone.status == "active"


def test_clone_name_derivation_variants():
    source = _agent(name="审查员")
    first = clone_agent(source, owner_id="o", used_names=set())
    assert first.name == "审查员 副本"
    second = clone_agent(source, owner_id="o", used_names={"审查员 副本"})
    assert second.name == "审查员 副本2"
    third = clone_agent(
        source, owner_id="o", used_names={"审查员 副本", "审查员 副本2"}
    )
    assert third.name == "审查员 副本3"


def test_clone_name_truncates_long_base():
    long_name = "x" * 100
    source = _agent(name=long_name)
    clone = clone_agent(source, owner_id="o", used_names=set())
    assert len(clone.name) <= 100
    assert clone.name.endswith(" 副本")


def test_clone_gives_up_after_ten_attempts():
    source = _agent(name="A")
    used = {f"A 副本{i}" if i > 1 else "A 副本" for i in range(1, 11)}
    with pytest.raises(AppError, match="副本"):
        clone_agent(source, owner_id="o", used_names=used)


def test_clone_does_not_mutate_the_source():
    source = _agent()
    clone_agent(source, owner_id="owner-1", used_names=set())
    assert source.name == "源 Agent"
    assert source.temperature == 0.9


def test_clone_tools_list_is_copied_not_shared():
    """list 是可变对象：共享会让后续改克隆体时顺手改掉源 Agent。"""
    source = _agent(tools=["run_python", "calculate"])
    clone = clone_agent(source, owner_id="o", used_names=set())
    clone.tools.append("read_url")
    assert source.tools == ["run_python", "calculate"]
