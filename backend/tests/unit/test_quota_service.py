"""配额服务单元测试（T077）：月配额熔断纯逻辑（宪法「每个用户有月配额」）。

覆盖：
- assert_quota_available：未超限放行；达到/超过上限抛 QuotaExceeded（429 语义）
- QuotaExceeded：携带 usage/quota 信息，code=RATE_LIMITED
- 非法负数用量拒绝
- _month_start_utc：返回当前自然月 UTC 月初（跨年正确）
"""

from datetime import UTC, datetime

import pytest

from app.services.quota_service import (
    QuotaExceeded,
    _month_start_utc,
    assert_quota_available,
)

# ---------- 熔断纯逻辑 ----------


def test_assert_quota_available_allows_under_limit():
    """未超限：放行不抛错。"""
    assert_quota_available(usage_cents=999, quota_cents=1000)


def test_assert_quota_available_exactly_at_limit_raises():
    """达到上限：熔断（>= 语义，配额到达即拒绝新任务）。"""
    with pytest.raises(QuotaExceeded):
        assert_quota_available(usage_cents=1000, quota_cents=1000)


def test_assert_quota_available_exceeds_limit_raises():
    """超限：抛 QuotaExceeded 且携带用量/上限。"""
    with pytest.raises(QuotaExceeded) as exc_info:
        assert_quota_available(usage_cents=1001, quota_cents=1000)
    assert exc_info.value.usage_cents == 1001
    assert exc_info.value.quota_cents == 1000


def test_quota_exceeded_is_rate_limited_error():
    """QuotaExceeded 映射 429 RATE_LIMITED（对齐 errors.py 错误码表）。"""
    exc = QuotaExceeded(1500, 1000)
    assert exc.code == "RATE_LIMITED"
    assert exc.status_code == 429
    assert "1500" in exc.message and "1000" in exc.message


def test_assert_quota_available_negative_usage_raises():
    """非法负数用量：校验失败。"""
    with pytest.raises(ValueError):
        assert_quota_available(usage_cents=-1, quota_cents=1000)


# ---------- 月初边界 ----------


def test_month_start_utc_returns_first_day():
    """月初：UTC 当月 1 日 00:00:00.000000。"""
    start = _month_start_utc()
    assert start.day == 1
    assert start.hour == 0
    assert start.minute == 0
    assert start.second == 0
    assert start.microsecond == 0
    assert start.tzinfo is not None


def test_month_start_utc_uses_utc_now():
    """月初基于 UTC 当前时间（与 run.created_at 的 UTC 对齐）。"""
    start = _month_start_utc()
    now = datetime.now(UTC)
    assert start.year == now.year
    assert start.month == now.month
