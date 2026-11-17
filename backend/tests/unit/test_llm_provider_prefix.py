"""LiteLLM provider 前缀补齐：自建 OpenAI 兼容端点必须显式带 provider。

回归点：接第三方端点（`LLM_BASE_URL`）时，Agent 的 model 字段是端点自己的
模型名（如 `Deepseek-V4.1-Flash`），没有 `openai/` 前缀。LiteLLM 只认前缀选
provider，会直接报 "LLM Provider NOT provided" —— 表现为 run 一开始就失败。
"""

from app.executors.llm import litellm_model_name


def test_prefixes_bare_model_when_custom_endpoint_configured():
    assert (
        litellm_model_name("Deepseek-V4.1-Flash", "https://token.ai-hf.cn/v1")
        == "openai/Deepseek-V4.1-Flash"
    )


def test_keeps_explicit_provider_prefix():
    assert (
        litellm_model_name("anthropic/claude-3-5-sonnet", "https://x.test/v1")
        == "anthropic/claude-3-5-sonnet"
    )


def test_keeps_bare_model_when_no_custom_endpoint():
    """直连官方 OpenAI 时不能加前缀，否则模型名被改写。"""
    assert litellm_model_name("gpt-4o-mini", "") == "gpt-4o-mini"


def test_empty_model_is_passed_through():
    assert litellm_model_name("", "https://token.ai-hf.cn/v1") == ""
