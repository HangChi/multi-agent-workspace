"""工具目录一致性测试。

`catalog.py` 是工具定义的单一事实源。R5 之前，三个内置执行器各自手写了一份
与 catalog 逐字相同的 schema 字面量，本测试靠「两份比对」防漂移——但那只能
证明这一刻没漂移：改参数时仍然要改两处，漏一处的表现是模型看到的参数与
执行器真正解析的参数不一致。

R5 改成执行器 `schema()` 从 `to_openai_schema()` 派生。此时「两份比对」
在数学上恒真、已经没有信息量；本测试改为证明**派生关系本身成立**：
替换 CATALOG 中的定义，执行器的 schema 必须跟着变。谁把某个执行器改回
手写字面量，这条会红。
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
