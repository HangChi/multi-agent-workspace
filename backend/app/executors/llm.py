"""LiteLLM 封装（T066，US5）：chat 调用、tool_call 解析、usage→cost 换算。

对齐 R-04 成本熔断（FR-019）：每次调用后从 usage 累计成本（分），
按模型单价表（可配置，默认 gpt-4o-mini 等）换算。
"""

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any

import litellm

from app.core import metrics
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
    """归一化响应：content + tool_calls + 成本（分 / 微分）。"""

    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    cost_cents: int = 0
    # 精确成本（1 分 = 1e6 微分）：cost_cents 是它的四舍五入结果
    cost_micros: int = 0
    needs_input: bool = False
    needs_input_message: str = ""


# 模型单价表：每 1K token 成本（美元）。元组 (input, output)。
#
# 全部取**厂商官方刊例价**，来源、档位与核对日期见 docs/llm-pricing.md。
# 人民币价按 1 USD ≈ 7.0 CNY 折算（聚合网关的实际售价通常另算，这里只求量级对）。
# key 一律小写；端点上的变体名（`Deepseek-V4.1-Flash-1017`）按最长前缀匹配。
_MODEL_PRICES_USD_PER_1K: dict[str, tuple[float, float]] = {
    # OpenAI（直连官方时用）
    "gpt-4o-mini": (0.00015, 0.0006),
    "gpt-4o": (0.0025, 0.01),
    "gpt-4.1-mini": (0.0004, 0.0016),
    "gpt-4.1": (0.002, 0.008),
    # DeepSeek（已停用的旧模型名保留，避免历史数据回退到 gpt-4o-mini 单价）
    "deepseek-chat": (0.00027, 0.0011),
    "deepseek-reasoner": (0.00055, 0.00219),
    # 2026-11 接入的端点：官方 1 / 2 元 per 1M
    "deepseek-v4.1-flash": (0.000143, 0.000286),
    # 官方 3 / 6 元 per 1M（2.5 折优惠期，原价 12 / 24）
    "deepseek-v4-pro": (0.000429, 0.000857),
    # 智谱：官方 8 / 28 元 per 1M
    "glm-5.3": (0.001143, 0.004),
    # 智谱：官方 0.8 / 2.8 元 per 1M
    "glm-5.3-flash": (0.000114, 0.0004),
    # 月之暗面：官方 20 / 100 元 per 1M（缓存命中 2 元）
    "kimi-k3": (0.002857, 0.014286),
    # 阿里：官方 0.15 / 1.5 元 per 1M（输入 ≤128K 档）
    "qwen-flash": (0.0000214, 0.000214),
    # 阿里：官方美元价（华北2）$1.65 / $4.951 per 1M
    "qwen3.8-max": (0.00165, 0.004951),
    # 字节：官方 1.6 / 8.0 元 per 1M（输入 ≤32K 档）
    "doubao-seed-2.0-pro": (0.000229, 0.001143),
}

_FALLBACK_PRICE = _MODEL_PRICES_USD_PER_1K["gpt-4o-mini"]


def _price_for_model(model: str) -> tuple[float, float]:
    """取模型单价。

    精确匹配 → 最长前缀匹配（端点会带 `-1017` 这类版本后缀）→ 回退 gpt-4o-mini。
    回退是保守估计：未知模型按偏贵的单价算，熔断只会更早触发。
    """
    key = model.split("/", 1)[-1].strip().lower()
    exact = _MODEL_PRICES_USD_PER_1K.get(key)
    if exact is not None:
        return exact
    for name in sorted(_MODEL_PRICES_USD_PER_1K, key=len, reverse=True):
        if key.startswith(name):
            return _MODEL_PRICES_USD_PER_1K[name]
    return _FALLBACK_PRICE


def litellm_model_name(model: str, base_url: str) -> str:
    """给 LiteLLM 补齐 provider 前缀。

    LiteLLM 靠模型名的前缀选 provider，不认 `llm_base_url` 自己推断。接自建或
    第三方 OpenAI 兼容端点时，模型名（如 `Deepseek-V4.1-Flash`）没有前缀，会直接
    报 "LLM Provider NOT provided"。只在**这一层**补 `openai/`：数据库里的
    `Agent.model`、成本表与指标标签仍用不带前缀的干净名字。
    """
    if not model or "/" in model or not base_url:
        return model
    return f"openai/{model}"


def usage_to_cost_micros(model: str, usage: dict[str, Any] | None) -> int:
    """把 usage 换算成**微分**（1 分 = 1e6 微分）。

    为什么不直接算「分」：gpt-4o-mini 一次 1k prompt ≈ 0.015 分，`round()` 到分
    会记成 0 —— 多步小调用累计下来成本被系统性低估，配额与熔断都会偏松。
    微分是整数，既能精确累计，又不需要浮点比较。
    """
    if not usage:
        return 0
    input_tokens = int(usage.get("prompt_tokens") or 0)
    output_tokens = int(usage.get("completion_tokens") or 0)
    in_price, out_price = _price_for_model(model)
    cost_usd = (input_tokens * in_price + output_tokens * out_price) / 1000
    return max(0, round(cost_usd * 100 * 1_000_000))


def usage_to_cost_cents(model: str, usage: dict[str, Any] | None) -> int:
    """把 usage 换算为成本（分，四舍五入到整数分）。

    只为兼容既有调用方保留；需要累计时请用 `usage_to_cost_micros`。
    """
    return round(usage_to_cost_micros(model, usage) / 1_000_000)


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
        timeout_seconds: float | None = None,
    ) -> None:
        self.api_key = api_key or settings.llm_api_key
        self.base_url = base_url or settings.llm_base_url
        self.model = model or settings.llm_model
        self.timeout_seconds = timeout_seconds or settings.llm_timeout_seconds
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
            LLMClientError: 连接/鉴权/限流/超时等失败（转为可读错误）
        """
        kwargs: dict[str, Any] = {
            "model": litellm_model_name(self.model, self.base_url),
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "api_key": self.api_key,
            # 超时必须显式传：SDK 默认等待时间远超一个 Run 的合理耗时，
            # 而本循环的超时检查只在轮次之间生效，拦不住卡在 HTTP 里的调用。
            "timeout": self.timeout_seconds,
            # 重试一次足够：再多的重试只是在放大 Worker 被占用的时间
            "num_retries": 1,
        }
        if self.base_url:
            kwargs["api_base"] = self.base_url
        if tools:
            kwargs["tools"] = tools

        started = time.perf_counter()
        try:
            # 硬上限：SDK 的分阶段超时偶尔会有偏差，这里再兜一层
            response = await asyncio.wait_for(
                litellm.acompletion(**kwargs),
                timeout=self.timeout_seconds + 15,
            )
        except TimeoutError as exc:
            metrics.LLM_CALLS.inc(model=self.model, outcome="timeout")
            raise LLMClientError(
                f"LLM 调用超时（{self.timeout_seconds:.0f}s）"
            ) from exc
        except Exception as exc:  # 网络/鉴权/限流统一转可读错误
            metrics.LLM_CALLS.inc(model=self.model, outcome="error")
            raise LLMClientError(f"LLM 调用失败: {exc}") from exc

        metrics.LLM_CALLS.inc(model=self.model, outcome="ok")
        metrics.LLM_DURATION.observe(time.perf_counter() - started, model=self.model)
        metrics.LLM_COST.inc(
            usage_to_cost_micros(self.model, getattr(response, "usage", None))
            / 1_000_000,
            model=self.model,
        )

        message = response.choices[0].message
        usage = response.usage

        content = message.get("content") or ""
        tool_calls: list[ToolCall] = []
        for tc in message.get("tool_calls") or []:
            # litellm 返回 ChatCompletionMessageToolCall 对象（非 dict）：
            # function 可能是对象或 dict，arguments 可能是 dict 或 JSON 字符串
            fn = tc.get("function") or {}
            name = (
                fn.get("name", "")
                if isinstance(fn, dict)
                else getattr(fn, "name", "") or ""
            )
            raw_args = (
                fn.get("arguments")
                if isinstance(fn, dict)
                else getattr(fn, "arguments", "")
            )
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
            cost_micros=usage_to_cost_micros(self.model, usage),
            needs_input=needs_input,
            needs_input_message=needs_input_message,
        )
