"""重试策略纯函数（T006/T007）。

这里锁死三条**不可直觉化**的规则：

1. **未知异常按业务类**（research R-05）：对代码 bug 重试 3 次只是 3 倍日志
   噪声与 3 倍可能已产生的部分成本。瞬时故障的特征是明确的，白名单能列全。
2. **full jitter 而非固定退避**（research R-02）：同时失败的任务如果在同一秒
   一起重试，会把刚恢复的网关再次打挂。等待时长必须在 `[0, 计算值]` 内随机。
3. **执行超时不算瞬时**：任务连自己的时间预算都没走完，重试一次大概率还是超。
"""

import random

import pytest

from app.executors.llm import TRANSIENT_LITELLM_ERRORS, LLMClientError
from app.orchestrator import retry_policy
from app.orchestrator.retry_policy import (
    FailureKind,
    backoff_seconds,
    classify_failure,
    describe_kind,
    is_retryable,
    retry_delay_for,
)
from app.orchestrator.runner import (
    CostLimitExceeded,
    ExecutionTimeout,
    MaxStepsExceeded,
    NeedsInput,
)
from app.services.quota_service import QuotaExceeded

# ---------- 瞬时类：确知会自愈的失败 ----------


@pytest.mark.parametrize(
    "exc",
    [
        ConnectionError("Connection refused by gateway"),
        ConnectionResetError("Connection reset by peer"),
        TimeoutError("read timeout"),
        TimeoutError(),  # asyncio 超时在 3.11+ 即 TimeoutError
        LLMClientError("LLM 调用超时（30s）", transient=True),
        LLMClientError("LLM 调用失败: Connection error", transient=True),
    ],
)
def test_transient_failures_are_classified_as_transient(exc):
    assert classify_failure(exc) is FailureKind.TRANSIENT


# ---------- 业务类：重试一次还是同样结果 ----------


@pytest.mark.parametrize(
    "exc",
    [
        QuotaExceeded("QUOTA_EXCEEDED", "月配额已用尽"),
        CostLimitExceeded("超过成本上限"),
        MaxStepsExceeded("超过最大步数"),
        ExecutionTimeout("超过最大运行时长"),
        NeedsInput("需要补充信息"),
        # 鉴权/限流类：换密钥或等额度，重试同一个请求不会好
        LLMClientError("LLM 调用失败: Invalid API key"),
        LLMClientError("LLM 调用失败: Rate limit exceeded"),
    ],
)
def test_business_failures_are_classified_as_permanent(exc):
    assert classify_failure(exc) is FailureKind.PERMANENT


@pytest.mark.parametrize(
    "exc",
    [
        AttributeError("'NoneType' object has no attribute 'id'"),
        ValueError("unexpected value"),
        RuntimeError("boom"),
        KeyError("missing"),
        ZeroDivisionError("division by zero"),
    ],
)
def test_unknown_failures_fall_back_to_permanent(exc):
    """未知异常按业务类处理（research R-05 的保守原则）。"""
    assert classify_failure(exc) is FailureKind.PERMANENT


def test_none_exception_is_permanent():
    """防御性分支：调用方误传 None 时不崩，按业务类处理。"""
    assert classify_failure(None) is FailureKind.PERMANENT


# ---------- 退避：指数 + full jitter + 封顶 ----------


def test_backoff_grows_exponentially_with_attempt():
    """上限随 attempt 指数增长：第 N 次尝试的理论最大值是 base × 2^(N-1)。"""
    base, cap = 2.0, 60.0
    expected_max = [2.0, 4.0, 8.0, 16.0]
    for attempt, want in enumerate(expected_max, start=1):
        # full jitter 的上界就是理论计算值；取满区间验证不超
        samples = [backoff_seconds(attempt, base, cap) for _ in range(200)]
        assert max(samples) <= want + 1e-9, f"attempt={attempt} 超过理论上界"
        # 且有样本接近上界（否则可能是固定小值，没真做指数）
        assert max(samples) > want * 0.5, f"attempt={attempt} 未体现指数增长"


def test_backoff_uses_full_jitter_not_fixed_value():
    """同一 attempt 多次调用结果必须不同（否则就是固定退避，会惊群）。"""
    values = {backoff_seconds(2, 2.0, 60.0) for _ in range(50)}
    assert len(values) > 1, "full jitter 失效：返回值恒定"


def test_backoff_is_within_zero_and_computed_cap():
    for attempt in range(1, 6):
        value = backoff_seconds(attempt, 2.0, 60.0)
        assert 0.0 <= value <= 60.0


def test_backoff_respects_the_cap():
    """attempt 很大时不得无限增长，必须被 cap 截断。"""
    assert backoff_seconds(20, 2.0, 60.0) <= 60.0
    assert backoff_seconds(100, 2.0, 60.0) <= 60.0


def test_backoff_first_attempt_equals_base_at_the_upper_bound():
    """第一次重试的理论等待就是基准值（不翻倍）。"""
    assert max(backoff_seconds(1, 2.0, 60.0) for _ in range(200)) <= 2.0


def test_backoff_is_deterministic_under_seeded_random():
    """播种后可复现——测试与调试都需要这个性质。"""
    random.seed(42)
    first = backoff_seconds(3, 2.0, 60.0)
    random.seed(42)
    second = backoff_seconds(3, 2.0, 60.0)
    assert first == second


@pytest.mark.parametrize("attempt", [0, -1])
def test_backoff_rejects_non_positive_attempt(attempt):
    """attempt 从 1 开始计数；0 或负数是调用方 bug，直接炸而不是返回假值。"""
    with pytest.raises(ValueError, match="attempt"):
        backoff_seconds(attempt, 2.0, 60.0)


@pytest.mark.parametrize("base,cap", [(0, 60), (-1, 60), (2, 0), (2, -5)])
def test_backoff_rejects_invalid_config(base, cap):
    """基准与封顶必须是正数，且封顶不小于基准——否则配置错了会静默退避成 0。"""
    with pytest.raises(ValueError):
        backoff_seconds(1, float(base), float(cap))


# ---------- litellm 网关侧错误的分类（006 实测缺口补测） ----------
# 这条不是理论推演：真实栈验收时 mock LLM 返回 500，litellm 抛
# InternalServerError，而最初的实现只认连接类异常，导致 5xx 被当成业务类
# 直接终态——spec FR-003 明确要求「网关返回 5xx」是可重试的。


def _litellm_exc(cls) -> BaseException:
    """构造 litellm 异常实例。

    用 `__new__` 绕过 `__init__`：这些异常的构造函数签名各版本不一致
    （有的要 llm_provider/model，有的还要一个能访问 `.request` 的 response），
    而本测试只关心**类型**是否被正确分类，不需要一个完整初始化的实例。
    """
    return cls.__new__(cls)


@pytest.mark.parametrize(
    "cls_name",
    [
        "InternalServerError",
        "BadGatewayError",
        "ServiceUnavailableError",
        "APIConnectionError",
        "Timeout",
        "RateLimitError",
    ],
)
def test_litellm_gateway_errors_are_transient(cls_name: str):
    """网关 5xx / 连接 / 超时 / 限流 → 瞬时（等一会儿会好，退避正好治它）。"""
    import litellm.exceptions as lite

    exc = _litellm_exc(getattr(lite, cls_name))
    wrapped = LLMClientError(
        "LLM 调用失败", transient=isinstance(exc, TRANSIENT_LITELLM_ERRORS)
    )
    assert wrapped.transient is True, cls_name
    assert classify_failure(wrapped) is FailureKind.TRANSIENT


@pytest.mark.parametrize(
    "cls_name",
    [
        "AuthenticationError",
        "PermissionDeniedError",
        "NotFoundError",
        "BadRequestError",
        "ContextWindowExceededError",
    ],
)
def test_litellm_request_errors_are_permanent(cls_name: str):
    """鉴权/权限/不存在/请求格式 → 业务类（重试同一个请求不会好）。"""
    import litellm.exceptions as lite

    exc = _litellm_exc(getattr(lite, cls_name))
    wrapped = LLMClientError(
        "LLM 调用失败", transient=isinstance(exc, TRANSIENT_LITELLM_ERRORS)
    )
    assert wrapped.transient is False, cls_name
    assert classify_failure(wrapped) is FailureKind.PERMANENT


def test_transient_litellm_errors_is_non_empty():
    """导入失败时集合为空——那样 5xx 会被误判成业务类，必须被发现。"""
    assert len(TRANSIENT_LITELLM_ERRORS) >= 5


# ---------- 015 覆盖率补齐：重试门槛与等待时长选择 ----------


def test_is_retryable_requires_transient_failure(monkeypatch):
    """三个条件必须同时满足：瞬时类 + 未到上限 + 上限本身合法。"""
    assert is_retryable(ConnectionError("reset"), attempt=1, max_attempts=3) is True
    # 业务类失败：一次都不重试
    assert is_retryable(QuotaExceeded(1, 2), attempt=1, max_attempts=3) is False


def test_is_retryable_false_when_attempts_exhausted():
    """attempt 语义是「已经跑完第几次」，等于上限时不再重试。

    写成 `>` 会让每个 Run 多跑一次——多出来的那一次往往发生在配额刚好
    用尽的边缘，用户看到的是「明明说 3 次，跑了 4 次」。
    """
    assert is_retryable(ConnectionError("reset"), attempt=2, max_attempts=3) is True
    assert is_retryable(ConnectionError("reset"), attempt=3, max_attempts=3) is False
    assert is_retryable(ConnectionError("reset"), attempt=9, max_attempts=3) is False


@pytest.mark.parametrize("max_attempts", [0, 1])
def test_is_retryable_false_when_auto_retry_disabled(max_attempts):
    """max_attempts <= 1 等于关闭自动重试：配置成 1 时不能还偷偷重试一次。"""
    assert is_retryable(ConnectionError("x"), attempt=1, max_attempts=max_attempts) is (
        False
    )


def test_retry_delay_for_returns_zero_for_permanent_failures():
    """不可重试的失败等待时长为 0——调用方靠它区分「等待中」与「已终结」。"""
    assert (
        retry_delay_for(MaxStepsExceeded("too many"), 1, base_seconds=5, cap_seconds=60)
        == 0.0
    )


def test_retry_delay_for_uses_backoff_for_transient_failures(monkeypatch):
    """瞬时失败走 full jitter 退避，且上界随 attempt 翻倍。"""
    ceilings: list[float] = []

    def _fake_uniform(_low: float, high: float) -> float:
        ceilings.append(high)
        return high

    monkeypatch.setattr(retry_policy.random, "uniform", _fake_uniform)

    first = retry_policy.retry_delay_for(
        ConnectionError("reset"), 1, base_seconds=5, cap_seconds=60
    )
    second = retry_policy.retry_delay_for(
        ConnectionError("reset"), 2, base_seconds=5, cap_seconds=60
    )
    capped = retry_policy.retry_delay_for(
        ConnectionError("reset"), 9, base_seconds=5, cap_seconds=60
    )

    assert (first, second, capped) == (5.0, 10.0, 60.0)
    assert ceilings == [5.0, 10.0, 60.0]


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (ConnectionError("reset"), "transient"),
        (QuotaExceeded(1, 2), "permanent"),
    ],
)
def test_describe_kind_is_the_enum_value_used_in_logs(exc, expected):
    """日志与审计里写的是枚举值本身（transient/permanent），不是类名。"""
    assert describe_kind(exc) == expected
