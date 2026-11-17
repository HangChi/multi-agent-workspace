"""内置模板种子脚本的纯函数部分（T014）。

数据库侧的幂等（重复执行不新增行）在集成测试里验；这里锁定
「payload 构造」这条不会碰 IO 的逻辑：

- 模型来自参数，不读环境（脚本负责把 settings 传进来，函数保持纯）；
- 工具集与「当前启用项」取交集，下线工具不会让模板作废；
- 4 个模板名称互不重复（重名会让部分唯一索引把第二次播种整个拒掉）；
- 每条提示词非空且包含可执行指令（防止有人塞一段占位文本进去）。
"""

from app.executors.catalog import CATALOG
from app.schemas.agent_template import AgentTemplateCreate
from scripts.seed_agent_templates import builtin_payloads

ALL_CATALOG = sorted(CATALOG)


def test_four_builtins_with_unique_names():
    payloads = builtin_payloads(model="m", enabled_tools=ALL_CATALOG)
    assert len(payloads) == 4
    names = [p.name for p in payloads]
    assert len(set(names)) == 4


def test_model_comes_from_the_argument_not_the_environment():
    payloads = builtin_payloads(model="some-endpoint-model", enabled_tools=ALL_CATALOG)
    assert all(p.model == "some-endpoint-model" for p in payloads)


def test_tools_are_intersected_with_enabled_catalog():
    payloads = builtin_payloads(model="m", enabled_tools=["web_search"])
    by_name = {p.name: p for p in payloads}
    assert by_name["通用问答助手"].tools == ["web_search"]
    assert by_name["资料检索助手"].tools == ["web_search"]
    # 代码审查助手依赖 run_python，未启用时工具集为空而不是塞进未启用项
    assert by_name["代码审查助手"].tools == []


def test_offline_tool_does_not_invalidate_the_template():
    """只启用一半工具时，4 个模板仍然全部构造成功（FR-008 的哲学）。"""
    half = ALL_CATALOG[: len(ALL_CATALOG) // 2]
    payloads = builtin_payloads(model="m", enabled_tools=half)
    assert len(payloads) == 4
    for p in payloads:
        assert set(p.tools) <= set(half)


def test_prompts_are_actionable_not_placeholders():
    payloads = builtin_payloads(model="m", enabled_tools=ALL_CATALOG)
    for p in payloads:
        assert isinstance(p, AgentTemplateCreate)
        assert len(p.system_prompt) >= 50
        assert p.description


def test_payloads_pass_schema_validation_without_env():
    """payload 必须是合法的 AgentTemplateCreate —— 播种失败大多死在这一步。"""
    payloads = builtin_payloads(model="m", enabled_tools=ALL_CATALOG)
    for p in payloads:
        assert p.cost_limit_cents >= 1
        assert 0.0 <= p.temperature <= 2.0
        assert p.egress_mode in {"default", "restricted"}
