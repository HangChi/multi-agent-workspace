"""模型单价表：精确 / 最长前缀 / 回退三级匹配。

回归点：端点会把同族模型列成带后缀的名字（`Deepseek-V4.1-Flash-1017`）。
若只做精确匹配，这些变体全部回退到 gpt-4o-mini 单价，成本与熔断基准静默偏移。
"""

from app.executors.llm import _price_for_model

GPT4O_MINI = (0.00015, 0.0006)


def test_exact_match_is_lowercase_insensitive():
    assert _price_for_model("Deepseek-V4.1-Flash") == (0.000143, 0.000286)
    assert _price_for_model("kimi-k3") == (0.002857, 0.014286)


def test_version_suffix_inherits_family_price():
    assert _price_for_model("Deepseek-V4.1-Flash-1017") == (0.000143, 0.000286)
    assert _price_for_model("qwen-flash-2025-07-28") == (0.0000214, 0.000214)


def test_longest_prefix_wins_over_shorter_family():
    """`glm-5.3` 是 `glm-5.3-flash` 的前缀，必须是 flash 的价而不是旗舰价。"""
    assert _price_for_model("GLM-5.3-Flash") == (0.000114, 0.0004)
    assert _price_for_model("GLM-5.3") == (0.001143, 0.004)


def test_provider_prefix_is_stripped():
    assert _price_for_model("openai/Kimi-K3") == (0.002857, 0.014286)


def test_unknown_model_falls_back_to_gpt_4o_mini():
    assert _price_for_model("some-unlisted-model") == GPT4O_MINI
