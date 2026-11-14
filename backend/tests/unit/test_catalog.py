"""工具目录一致性测试。

`catalog.py` 是工具定义的单一事实源，但它的 `param_schema` 是**手写数据**，
不是从执行器反射出来的。手写就会漂移：改了执行器的参数名忘了改 catalog，
模型看到的工具定义与真正执行时的解析就对不上。

本测试反向校验两者逐字一致——这是 catalog 设计成立的前提，缺了它
「单一事实源」只是一句口号。
"""

import pytest

from app.executors.base import ExecutorRegistry
from app.executors.catalog import CATALOG, LEGACY_TOOL_NAMES, get_tool, to_openai_schema

# 已实现执行器的工具 → 注册表能按名取到
IMPLEMENTED = ["web_search", "read_url", "calculate"]


def _executor(name: str):
    """按名取执行器实例（未实现则 skip，不算失败）。"""
    registry = ExecutorRegistry.from_names([name])
    ex = registry.get(name)
    if ex is None:
        pytest.skip(f"{name} 的执行器尚未实现")
    return ex


@pytest.mark.parametrize("name", IMPLEMENTED)
def test_catalog_schema_matches_executor(name: str) -> None:
    """catalog 的 OpenAI schema 必须与执行器 `schema()` 完全一致。"""
    spec = CATALOG[name]
    expected = {
        "type": "function",
        "function": {
            "name": spec.name,
            "description": spec.description,
            "parameters": spec.param_schema,
        },
    }
    assert to_openai_schema(name) == expected
    # 关键断言：执行器自己声明的形状与 catalog 逐字相同
    assert _executor(name).schema() == expected, (
        f"{name} 的 catalog 定义与执行器 schema 漂移——"
        "改了执行器参数就必须同步改 catalog.py"
    )


def test_catalog_names_are_unique_and_wellformed() -> None:
    """工具名非空、无空格、且 key 与 name 字段一致（防手写错位）。"""
    for key, spec in CATALOG.items():
        assert spec.name == key, f"CATALOG key {key!r} 与 ToolSpec.name {spec.name!r} 不一致"
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
            assert spec.timeout_seconds >= 60, f"{spec.name} 是沙箱工具，超时不应小于 60s"


def test_catalog_param_schema_shape() -> None:
    """param_schema 必须是合法 JSON Schema 对象段，required 里的键必须存在于 properties。"""
    for spec in CATALOG.values():
        schema = spec.param_schema
        assert schema.get("type") == "object", f"{spec.name} 的 param_schema 缺 type=object"
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
