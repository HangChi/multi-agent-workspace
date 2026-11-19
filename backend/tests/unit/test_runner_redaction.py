"""步骤落库前的脱敏逻辑单测（015 覆盖率补齐）。

`_step_input` / `_redact_tool_calls` 决定「什么会进数据库」。它们是隐私与体积的
双重闸门，而且**失败是静默的**——不脱敏不会报错，只会在某天发现沙箱源码或第三方
工具的完整返回被永久存进了 runs 表：

- 沙箱工具的 source（LLM 生成的代码）只留在 sandbox_executions，不重复存步骤里；
- MCP 工具返回可能很大也可能含敏感数据，只保留 status/是否截断；
- 解析不出结构的外部工具输出，整段丢弃（宁可不显示，也不落原文）。

`_jsonable` 则是为了「执行器返回的东西一定存得下」：dataclass、tuple、任意对象
都要能变成可 JSON 序列化的结构，否则写库时才会炸。
"""

from dataclasses import dataclass

from app.orchestrator.runner import (
    _jsonable,
    _redact_tool_calls,
    _step_input,
)

# ---------- _redact_tool_calls ----------


def test_redacts_sandbox_source():
    calls = [{"name": "run_python", "arguments": {"source": "print('secret')"}}]
    assert _redact_tool_calls(calls) == [
        {
            "name": "run_python",
            "arguments": {"source": "[stored in sandbox execution detail]"},
        }
    ]


def test_redacts_mcp_arguments_entirely():
    calls = [{"name": "mcp_search", "arguments": {"query": "内部客户名单"}}]
    assert _redact_tool_calls(calls) == [
        {"name": "mcp_search", "arguments": {"redacted": True}}
    ]


def test_keeps_other_tool_arguments():
    calls = [{"name": "calculate", "arguments": {"expression": "1+1"}}]
    assert _redact_tool_calls(calls)[0]["arguments"] == {"expression": "1+1"}


def test_non_list_and_non_dict_items_pass_through():
    assert _redact_tool_calls("plain") == "plain"
    assert _redact_tool_calls(
        [None, "x", {"name": "calculate", "arguments": {"a": 1}}]
    )[2]["arguments"] == {"a": 1}


# ---------- _step_input ----------


def test_sandbox_tool_content_replaced():
    message = {"role": "tool", "name": "run_python", "content": "完整 stdout..."}
    assert _step_input(message)["content"] == (
        "[sandbox output is available in execution detail]"
    )


def test_mcp_tool_content_reduced_to_status():
    import json as _json

    content = _json.dumps(
        {"status": "succeeded", "result_truncated": True, "data": "x" * 5000}
    )
    sanitized = _step_input({"role": "tool", "name": "mcp_search", "content": content})

    payload = sanitized["content"]
    assert "succeeded" in payload
    assert "result_truncated" in payload
    assert "x" * 100 not in payload  # 原始数据不落库


def test_mcp_tool_unparsable_content_dropped():
    sanitized = _step_input(
        {"role": "tool", "name": "mcp_search", "content": "{not json"}
    )
    assert sanitized["content"] == "[external tool output omitted]"


def test_assistant_tool_calls_are_redacted():
    message = {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"name": "run_shell", "arguments": {"source": "rm -rf /"}}],
    }
    sanitized = _step_input(message)

    assert sanitized["tool_calls"][0]["arguments"]["source"].startswith("[stored")


def test_plain_message_unchanged():
    message = {"role": "user", "content": "你好"}
    assert _step_input(message) == message


def test_non_dict_message_returned_as_is():
    assert _step_input("raw") == "raw"


# ---------- _jsonable ----------


@dataclass
class _Payload:
    status: str
    items: tuple


def test_jsonable_handles_nested_structures():
    value = _Payload(status="ok", items=(1, {"k": [1, 2]}))

    result = _jsonable(value)

    assert result == {"status": "ok", "items": [1, {"k": [1, 2]}]}


def test_jsonable_stringifies_unknown_objects():
    class _Opaque:
        def __str__(self) -> str:
            return "opaque"

    assert _jsonable(_Opaque()) == "opaque"


def test_jsonable_passes_scalars_and_none():
    assert _jsonable(None) is None
    assert _jsonable(3.5) == 3.5
    assert _jsonable(True) is True
