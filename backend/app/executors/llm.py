"""LiteLLM 封装（T066，US5）：chat 调用、tool_call 解析、usage→cost 换算。

对齐 R-04 成本熔断（FR-019）：每次调用后从 usage 累计成本（分），
按模型单价表（可配置，默认 gpt-4o-mini 等）换算。
"""

import json
from dataclasses import dataclass, field
from typing import Any

import litellm

from app.core.config import settings


class LLMClientError(Exception):
    """LLM 调用失败（连接/鉴权/限流等，转为可读错误）。"""


@dataclass
class ToolCall:
    """一次 tool_call（OpenAI 格式）。"""

    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResponse:
    """归一化响应：content + tool_calls + 成本（分）。"""

    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    cost_cents: int = 0
    needs_input: bool = False
    needs_input_message: str = ""


# 模型单价表：每 1K token 成本（美元）。元组 (input, output)。
# 以 gpt-4o-mini 为基准，可扩展更多模型。
_MODEL_PRICES_USD_PER_1K: dict[str, tuple[float, float]] = {
    "gpt-4o-mini": (0.00015, 0.0006),
    "gpt-4o": (0.0025, 0.01),
    "gpt-4.1-mini": (0.0004, 0.0016),
    "gpt-4.1": (0.002, 0.008),
    "deepseek-chat": (0.00027, 0.0011),
    "deepseek-reasoner": (0.00055, 0.00219),
}


def _price_for_model(model: str) -> tuple[float, float]:
    """取模型单价；未知模型按 gpt-4o-mini 保守估（防熔断失效）。"""
    return _MODEL_PRICES_USD_PER_1K.get(model, _MODEL_PRICES_USD_PER_1K["gpt-4o-mini"])


def usage_to_cost_cents(model: str, usage: dict[str, Any] | None) -> int:
    """把 LiteLLM usage（prompt/completion tokens）换算为成本（分）。

    cost_cents = (input_tokens × in_price + output_tokens × out_price) / 1000 × 100
    """
    if not usage:
        return 0
    input_tokens = int(usage.get("prompt_tokens") or 0)
    output_tokens = int(usage.get("completion_tokens") or 0)
    in_price, out_price = _price_for_model(model)
    cost_usd = (input_tokens * in_price + output_tokens * out_price) / 1000
    return max(0, round(cost_usd * 100))


def _extract_needs_input(message: dict[str, Any]) -> tuple[bool, str]:
    """识别「需要补充信息」信号：tool_call 名为 ask_user 或 content 含前缀。"""
    content = message.get("content") or ""
    if content.strip().startswith("__NEEDS_INPUT__"):
        return True, content.strip().removeprefix("__NEEDS_INPUT__").strip()
    return False, ""


class LiteLLMClient:
    """同步/异步 chat 封装：返回归一化 LLMResponse（含成本）。"""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
    ) -> None:
        self.api_key = api_key or settings.llm_api_key
        self.base_url = base_url or settings.llm_base_url
        self.model = model or settings.llm_model
        # 测试环境不配 key 时用假 key，避免 LiteLLM 启动报错
        if not self.api_key:
            self.api_key = "sk-test-placeholder"

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """调用 LLM，解析 tool_call + 累计成本。

        Raises:
            LLMClientError: 连接/鉴权/限流等失败（转为可读错误）
        """
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "api_key": self.api_key,
        }
        if self.base_url:
            kwargs["api_base"] = self.base_url
        if tools:
            kwargs["tools"] = tools

        try:
            response = await litellm.acompletion(**kwargs)
        except Exception as exc:  # 网络/鉴权/限流统一转可读错误
            raise LLMClientError(f"LLM 调用失败: {exc}") from exc

        message = response.choices[0].message
        usage = response.usage

        content = message.get("content") or ""
        tool_calls: list[ToolCall] = []
        for tc in message.get("tool_calls") or []:
            # litellm 返回 ChatCompletionMessageToolCall 对象（非 dict）：
            # function 可能是对象或 dict，arguments 可能是 dict 或 JSON 字符串
            fn = tc.get("function") or {}
            name = fn.get("name", "") if isinstance(fn, dict) else getattr(fn, "name", "") or ""
            raw_args = fn.get("arguments") if isinstance(fn, dict) else getattr(fn, "arguments", "")
            if isinstance(raw_args, str):
                try:
                    parsed = json.loads(raw_args)
                except (json.JSONDecodeError, TypeError):
                    parsed = {"raw": raw_args}
            elif isinstance(raw_args, dict):
                parsed = raw_args
            else:
                parsed = {}
            tool_calls.append(ToolCall(name=name, arguments=parsed))

        needs_input, needs_input_message = _extract_needs_input(message)

        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            cost_cents=usage_to_cost_cents(self.model, usage),
            needs_input=needs_input,
            needs_input_message=needs_input_message,
        )
