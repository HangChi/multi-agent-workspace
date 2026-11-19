"""外部服务器校验的纯函数与错误归类（015 覆盖率补齐）。

`validate_server` 是失败必须「fail closed」的关键路径：任何异常都要落一条
`safe_error`，且这条错误**绝不能泄露内网细节**。它的分类函数 `_safe_error`
是纯函数，最适合钉死——每个分类都对应一段「connection/auth 状态怎么写」
的下游逻辑，分错类会连累审计里的状态标签。

同时补 `_valid_tool`（工具发现时的字段白名单）与 `_stable_name`（不可变
工具名生成，决定前端/审计里看到的名字）。
"""

import pytest

from app.executors.mcp_client import MCPTool
from app.models.external_tool import ExternalToolServer
from app.services.external_validation_service import (
    _safe_error,
    _stable_name,
    _valid_tool,
)

# ---------- _safe_error：错误归类 ----------


@pytest.mark.parametrize(
    ("message", "expected_category"),
    [
        ("401 Unauthorized", "auth_error"),
        ("403 Forbidden", "auth_error"),
        ("authentication failed", "auth_error"),
        ("invalid credential supplied", "auth_error"),
    ],
)
def test_safe_error_classifies_auth_rejections(message, expected_category):
    category, safe = _safe_error(RuntimeError(message))
    assert category == "auth_error"
    # 认证失败绝不回显服务器的原文（可能含端点/用户名线索）
    assert message not in safe


def test_safe_error_classifies_timeouts():
    category, _ = _safe_error(TimeoutError("timed out"))
    assert category == "timeout"


def test_safe_error_classifies_transport_errors():
    import httpx

    from app.executors.mcp_client import MCPError

    assert _safe_error(MCPError("protocol broke"))[0] == "transport_error"
    assert _safe_error(httpx.ConnectError("conn refused"))[0] == "transport_error"


def test_safe_error_falls_back_to_generic_validation_error():
    """未知异常归入 validation_error：绝不往上抛，也不泄露 str(exc)。"""
    category, safe = _safe_error(KeyError("some internal detail"))
    assert category == "validation_error"
    assert safe == "MCP server validation failed"


def test_safe_error_messages_never_leak_exception_text():
    """safe_message 是固定文案，不含任何 str(exc) 片段。"""
    for exc in [RuntimeError("secret 10.0.0.1"), TimeoutError("host 10.0.0.2 down")]:
        _, safe = _safe_error(exc)
        assert "10.0.0" not in safe


# ---------- _valid_tool：工具字段白名单 ----------


def _tool(**overrides):
    base = {
        "name": "lookup",
        "description": "Lookup records",
        "input_schema": {"type": "object"},
    }
    base.update(overrides)
    return MCPTool(**base)


@pytest.mark.parametrize(
    "tool",
    [
        _tool(),
        _tool(name="a" * 150),  # 名字正好到上限
        _tool(description=""),
        _tool(output_schema=None),
    ],
)
def test_valid_tool_accepts_boundary_valid_shapes(tool):
    assert _valid_tool(tool) is True


@pytest.mark.parametrize(
    "tool",
    [
        "not-a-tool",
        _tool(name=""),
        _tool(name="a" * 151),  # 超长名
        _tool(description=None),
        _tool(description=123),  # 非字符串描述
        _tool(input_schema={"type": "array"}),  # schema 顶层必须是 object
        _tool(input_schema=[]),  # 非 dict schema
    ],
)
def test_valid_tool_rejects_invalid_shapes(tool):
    assert _valid_tool(tool) is False


# ---------- _stable_name：不可变工具名生成 ----------


def _server(name: str) -> ExternalToolServer:
    return ExternalToolServer(name=name)


@pytest.mark.parametrize(
    ("server_name", "external_name"),
    [
        ("My Server!", "Lookup Records"),
        ("   ", ""),  # 空名回退到 server/tool 占位
        ("Café ☕", "Get-User"),
    ],
)
def test_stable_name_sanitizes_to_slug_and_fits_200_chars(server_name, external_name):
    name = _stable_name(_server(server_name), external_name, "a" * 64)
    assert name.startswith("mcp_")
    assert len(name) <= 200
    assert " " not in name
    assert "!" not in name
    assert "☕" not in name


def test_stable_name_is_deterministic_given_same_hash():
    first = _stable_name(_server("My Server"), "Lookup", "hash1234")
    second = _stable_name(_server("My Server"), "Lookup", "hash1234")
    assert first == second


def test_stable_name_embeds_definition_hash_prefix():
    """名字必须包含定义摘要前 12 位——否则「同 server 同名不同 schema」会撞名。"""
    name = _stable_name(_server("srv"), "lookup", "abcdef1234567890")
    assert "abcdef123456" in name


def test_stable_name_truncates_long_server_and_tool_names():
    name = _stable_name(_server("s" * 200), "t" * 200, "h" * 64)
    assert len(name) <= 200
