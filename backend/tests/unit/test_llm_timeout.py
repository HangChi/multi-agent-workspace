"""LLM 客户端：必须带超时与有界重试。

回归点（2026-11-17 审计）：原实现调 `litellm.acompletion(**kwargs)` 时不传
`timeout`，而 runner 的超时检查只在「每轮循环之前」执行 —— 一次挂死的调用会
占住 Worker 槽位到 SDK 默认超时（可达 10 分钟）。默认并发 2，两个请求就能让
Worker 停摆，且 run 级 timeout 无法打断它。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.executors import llm as llm_mod
from app.executors.llm import LiteLLMClient, LLMClientError


def _response() -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message={"content": "最终答案: ok"})],
        usage={"prompt_tokens": 10, "completion_tokens": 5},
    )


def test_chat_passes_explicit_timeout_and_bounded_retries(monkeypatch):
    captured: dict = {}

    async def fake_acompletion(**kwargs):
        captured.update(kwargs)
        return _response()

    monkeypatch.setattr(llm_mod.litellm, "acompletion", fake_acompletion)
    client = LiteLLMClient(api_key="k", model="gpt-4o-mini", timeout_seconds=42)

    asyncio.run(client.chat([{"role": "user", "content": "hi"}]))

    assert captured["timeout"] == 42
    assert captured["num_retries"] == 1


def test_chat_maps_timeout_to_readable_error(monkeypatch):
    async def fake_acompletion(**kwargs):
        raise TimeoutError

    monkeypatch.setattr(llm_mod.litellm, "acompletion", fake_acompletion)
    client = LiteLLMClient(api_key="k", model="gpt-4o-mini", timeout_seconds=1)

    with pytest.raises(LLMClientError) as excinfo:
        asyncio.run(client.chat([{"role": "user", "content": "hi"}]))

    assert "超时" in str(excinfo.value)


def test_default_timeout_comes_from_settings(monkeypatch):
    client = LiteLLMClient(api_key="k", model="gpt-4o-mini")

    assert client.timeout_seconds == llm_mod.settings.llm_timeout_seconds
    assert client.timeout_seconds > 0
