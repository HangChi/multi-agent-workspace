"""工具目录一致性测试。

`catalog.py` 是工具定义的单一事实源。R5 之前，**四个**内置执行器各自手写了
一份与 catalog 相同的 schema——报告只数到三个（calc / read_url / http），
漏了 `sandbox.py`；它虽然从 `CATALOG[self.name]` 取值而非写字面量，但
「拼 OpenAI 形状」这件事仍另写了一遍。

本测试原先靠「两份比对」防漂移，那只能证明这一刻没漂移：改参数时仍然要改
两处，漏一处的表现是模型看到的参数与执行器真正解析的参数不一致。

R5 改成执行器 `schema()` 从 `to_openai_schema()` 派生。此时「两份比对」
在数学上恒真、已经没有信息量；本测试改为证明**派生关系本身成立**。注意
两条测试的分工（实测确认过，缺一不可）：

- `test_executor_schema_follows_catalog_definition`：改 **catalog 数据**，
  执行器跟着变。手写拼装也读 `CATALOG[self.name]`，所以它**拦不住**手写。
- `test_executor_calls_the_shared_schema_builder`：替换**拼装函数本身**，
  执行器必须跟着走。这条才拦得住手写——风险在形状（`function` 将来加字段）
  而不是数据。
"""

from dataclasses import replace

import pytest

from app.executors.base import ExecutorRegistry
from app.executors.catalog import CATALOG, LEGACY_TOOL_NAMES, get_tool, to_openai_schema

# 已实现执行器的工具 → 注册表能按名取到
IMPLEMENTED = list(CATALOG)


def _executor(name: str):
    """按名取执行器实例；目录新增工具时必须同步提供实现。"""
    registry = ExecutorRegistry.from_names([name])
    ex = registry.get(name)
    assert ex is not None, f"{name} 在目录中但没有真实执行器实现"
    return ex


@pytest.mark.parametrize("name", IMPLEMENTED)
def test_executor_schema_is_derived_from_catalog(name: str) -> None:
    """执行器 schema 必须与 catalog 派生结果一致。"""
    expected = to_openai_schema(name)
    assert expected is not None
    assert _executor(name).schema() == expected


@pytest.mark.parametrize("name", IMPLEMENTED)
def test_executor_schema_follows_catalog_definition(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """改 catalog 定义，执行器 schema 必须跟着变——这是「单一来源」的实证。

    手写字面量的执行器会在这里红：它读自己的 `_SCHEMA`，看不见 catalog 的改动。
    这正是 R5 之前的形态，也是 `param_schema` 漂移的成因。
    """
    modified = replace(
        CATALOG[name],
        description=f"{CATALOG[name].description}（测试注入）",
        param_schema={
            "type": "object",
            "properties": {"injected": {"type": "string"}},
            "required": ["injected"],
        },
    )
    monkeypatch.setitem(CATALOG, name, modified)

    schema = _executor(name).schema()
    assert schema["function"]["description"].endswith("（测试注入）")
    assert "injected" in schema["function"]["parameters"]["properties"]


def test_catalog_names_are_unique_and_wellformed() -> None:
    """工具名非空、无空格、且 key 与 name 字段一致（防手写错位）。"""
    for key, spec in CATALOG.items():
        assert spec.name == key, (
            f"CATALOG key {key!r} 与 ToolSpec.name {spec.name!r} 不一致"
        )
        assert spec.name and " " not in spec.name
        assert spec.display_name.strip()
        assert spec.description.strip()


def test_catalog_kind_is_known() -> None:
    """kind 只允许 builtin/sandbox，且 sandbox 类必须给足超时（不能沿用 10s 计算类默认值）。"""
    for spec in CATALOG.values():
        assert spec.kind in ("builtin", "sandbox")
        assert spec.timeout_seconds > 0
        if spec.kind == "sandbox":
            # 装包、跑数据处理很容易超过 30s；给太小会让正常脚本频繁超时
            assert spec.timeout_seconds >= 60, (
                f"{spec.name} 是沙箱工具，超时不应小于 60s"
            )


def test_catalog_param_schema_shape() -> None:
    """param_schema 必须是合法 JSON Schema 对象段，required 里的键必须存在于 properties。"""
    for spec in CATALOG.values():
        schema = spec.param_schema
        assert schema.get("type") == "object", (
            f"{spec.name} 的 param_schema 缺 type=object"
        )
        props = schema.get("properties")
        assert isinstance(props, dict) and props, f"{spec.name} 无 properties"
        for req in schema.get("required", []):
            assert req in props, f"{spec.name} 的 required 含未声明参数 {req!r}"


def test_legacy_names_do_not_collide_with_catalog() -> None:
    """旧名不能与现有工具名重叠，否则改名会自我覆盖。"""
    for legacy, mapped in LEGACY_TOOL_NAMES.items():
        assert legacy not in CATALOG, f"旧名 {legacy!r} 仍在 CATALOG 中"
        if mapped is not None:
            assert mapped in CATALOG, f"旧名 {legacy!r} 映射到不存在的工具 {mapped!r}"


def test_get_tool_unknown_returns_none() -> None:
    """未知工具返回 None，由调用方决定报错还是 warning（不抛异常）。"""
    assert get_tool("not_exist") is None
    assert to_openai_schema("not_exist") is None


def test_executor_schema_is_never_empty_for_catalog_tools() -> None:
    """`schema()` 用 `to_openai_schema(...) or {}` 兜底，但目录内工具不该走到兜底。

    走到兜底说明 `name` 与 CATALOG 的 key 不一致（拼错），那是个静默的空 schema
    ——LLM 会收到一个没有名字的工具定义。
    """
    for name in IMPLEMENTED:
        assert _executor(name).schema(), f"{name} 的 schema 为空，检查 name 是否拼错"


@pytest.mark.parametrize("name", IMPLEMENTED)
def test_executor_calls_the_shared_schema_builder(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """执行器必须**调用** `to_openai_schema`，而不是自己拼一份同样形状的 dict。

    这一条与 `test_executor_schema_follows_catalog_definition` 的区别值得说清：
    后者只证明「值相等」，而**手写拼装**也读 `CATALOG[self.name]`，所以改
    catalog 时它同样会变——两条都拦不住「另写一份拼装代码」。实测过：把
    `sandbox.schema()` 改回手写，前两条全绿。

    真正的风险不在数据，在**形状**：`_openai_schema` 将来若给 function 加字段
    （比如 `strict`），自己拼的那份不会跟着变，而模型侧看到的工具定义会静默
    少一块。所以这里直接替换拼装函数，看执行器是否跟着走。
    """
    calls: list[str] = []

    def fake_builder(target: str) -> dict[str, object]:
        calls.append(target)
        return {
            "type": "function",
            "function": {
                "name": f"replaced-{target}",
                "description": "由测试替换的拼装函数",
                "parameters": {"type": "object", "properties": {}},
            },
        }

    monkeypatch.setattr(
        f"{_executor(name).__class__.__module__}.to_openai_schema",
        fake_builder,
        raising=False,
    )
    schema = _executor(name).schema()

    assert calls == [name], (
        f"{name} 的 schema() 没有走共享拼装函数，可能自己拼了一份；"
        "那样 function 形状变化时它会静默落后"
    )
    assert schema["function"]["name"] == f"replaced-{name}"
