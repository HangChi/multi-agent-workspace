"""LiteLLMClient 的归一化与失败分类单测（015 覆盖率补齐）。

这个客户端承担两件容易出错的事：

1. **把 litellm 的多种返回形状归一化**。litellm 的 tool_call 里 `function`
   可能是 dict 也可能是对象，`arguments` 可能是 JSON 字符串、dict、甚至是
   None。少判一种就会在真实模型上崩——而单测里用假 response 正好能覆盖齐。
2. **判断失败是否可重试**（006 的重试策略依赖它）。鉴权/参数错误重试一万次
   也不会好，连接类错误退避一下就能好；把两者混为一谈会导致「无限重试一个
   永远失败的请求」或「一次抖动就放弃」。

`_extract_needs_input` 单独测：它是「Agent 主动向人提问」的识别点，前缀匹配
错一个字，整条 needs_input 链路就静默失效。
"""

import json
from types import SimpleNamespace

import pytest

from app.executors import llm as llm_module
from app.executors.llm import (
    LiteLLMClient,
    LLMClientError,
    _extract_needs_input,
)


def _response(*, content=None, tool_calls=None):
    """构造与 litellm 形状一致的假响应。

    message 用 **dict**：客户端代码走的是 `message.get("content")` /
    `tc.get("function")` 这套字典型访问（litellm 的 Message 也支持 .get），
    用 SimpleNamespace 会在第一步就 AttributeError。
    """
    message = {"content": content, "tool_calls": tool_calls}
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message)],
        # usage 用 dict：litellm 的 Usage 支持 .get，成本计算依赖这一点
        usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    )


@pytest.fixture
def client():
    return LiteLLMClient(api_key="k", base_url="http://llm.test", model="openai/x")


async def test_chat_parses_dict_function_with_json_arguments(monkeypatch, client):
    tool_call = {
        "function": {
            "name": "read_url",
            "arguments": json.dumps({"url": "https://a.test"}),
        }
    }
    monkeypatch.setattr(
        llm_module.litellm,
        "acompletion",
        _async_return(_response(tool_calls=[tool_call])),
    )

    result = await client.chat([{"role": "user", "content": "hi"}])

    assert result.tool_calls[0].name == "read_url"
    assert result.tool_calls[0].arguments == {"url": "https://a.test"}


async def test_chat_parses_object_function_with_dict_arguments(monkeypatch, client):
    """function 是对象（litellm 的 ChatCompletionMessageToolCall）时同样要能解。"""
    # function 是对象（litellm 的 ChatCompletionMessageToolCall.function）
    tool_call = {
        "function": SimpleNamespace(name="calculate", arguments={"expression": "1+1"})
    }
    monkeypatch.setattr(
        llm_module.litellm,
        "acompletion",
        _async_return(_response(tool_calls=[tool_call])),
    )

    result = await client.chat([{"role": "user", "content": "hi"}])

    assert result.tool_calls[0].arguments == {"expression": "1+1"}


async def test_chat_keeps_raw_when_arguments_are_not_json(monkeypatch, client):
    """模型偶尔会返回半截 JSON：保留原文而不是丢弃整次调用。"""
    tool_call = {"function": {"name": "read_url", "arguments": "{bad json"}}
    monkeypatch.setattr(
        llm_module.litellm,
        "acompletion",
        _async_return(_response(tool_calls=[tool_call])),
    )

    result = await client.chat([{"role": "user", "content": "hi"}])

    assert result.tool_calls[0].arguments == {"raw": "{bad json"}


async def test_chat_empty_arguments_when_value_missing(monkeypatch, client):
    tool_call = {"function": {"name": "noop", "arguments": None}}
    monkeypatch.setattr(
        llm_module.litellm,
        "acompletion",
        _async_return(_response(tool_calls=[tool_call])),
    )

    result = await client.chat([{"role": "user", "content": "hi"}])

    assert result.tool_calls[0].arguments == {}


async def test_chat_timeout_is_marked_transient(monkeypatch, client):
    async def _timeout(**kwargs):
        raise TimeoutError("slow")

    monkeypatch.setattr(llm_module.litellm, "acompletion", _timeout)

    with pytest.raises(LLMClientError) as exc:
        await client.chat([{"role": "user", "content": "hi"}])

    assert exc.value.transient is True
    assert "超时" in str(exc.value)


async def test_chat_auth_error_is_not_transient(monkeypatch, client):
    """鉴权失败重试无意义：必须标成不可重试，否则会一直烧配额。"""

    class _AuthError(Exception):
        pass

    async def _boom(**kwargs):
        raise _AuthError("invalid api key")

    monkeypatch.setattr(llm_module.litellm, "acompletion", _boom)

    with pytest.raises(LLMClientError) as exc:
        await client.chat([{"role": "user", "content": "hi"}])

    assert exc.value.transient is False


async def test_chat_connection_error_is_transient(monkeypatch, client):
    async def _boom(**kwargs):
        raise ConnectionResetError("reset by peer")

    monkeypatch.setattr(llm_module.litellm, "acompletion", _boom)

    with pytest.raises(LLMClientError) as exc:
        await client.chat([{"role": "user", "content": "hi"}])

    assert exc.value.transient is True


def test_extract_needs_input_prefix():
    needs, message = _extract_needs_input({"content": "__NEEDS_INPUT__ 需要你确认预算"})
    assert needs is True
    assert message == "需要你确认预算"


def test_extract_needs_input_absent():
    needs, message = _extract_needs_input({"content": "普通回答"})
    assert needs is False
    assert message == ""


def _async_return(value):
    async def _inner(**kwargs):
        return value

    return _inner
