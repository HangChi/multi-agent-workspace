"""cron 解析的错误路径单测（015 覆盖率补齐）。

正常路径已有覆盖；这里补的是**用户输错时说什么话**。cron 表达式的报错如果只
甩一句「不合法」，用户根本不知道该改哪个字段——所以 `_translate_parse_error`
把四种典型错误分别映射成了带字段范围的中文说明。这些分支在真实使用里一定会
被触发（尤其「2 月 31 日」这种），但正常路径的测试覆盖不到。
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.services.schedule_cron import (
    CronExpressionError,
    count_between,
    cron_weekdays,
    next_after,
    parse_cron,
    validate_cron,
)


def test_empty_expression_rejected():
    with pytest.raises(CronExpressionError) as exc:
        parse_cron("", "UTC")
    assert "5 个字段" in str(exc.value)


def test_wrong_field_count_reports_actual_count():
    with pytest.raises(CronExpressionError) as exc:
        parse_cron("1 2 3", "UTC")
    assert "当前是 3 个" in str(exc.value)


def test_out_of_range_field_mentions_ranges():
    with pytest.raises(CronExpressionError) as exc:
        parse_cron("61 * * * *", "UTC")
    message = str(exc.value)
    assert "0-59" in message or "超出范围" in message


def test_never_occurring_date_explained():
    """2 月 31 日：说得清「指定的日期在该月份不存在」，而不是笼统的非法。"""
    with pytest.raises(CronExpressionError) as exc:
        parse_cron("0 0 31 2 *", "UTC")
    assert "不会产生任何触发时刻" in str(exc.value)


def test_bad_weekday_literal_explained():
    with pytest.raises(CronExpressionError) as exc:
        parse_cron("0 0 * * 9", "UTC")
    assert "星期" in str(exc.value) or "周" in str(exc.value)


def test_bad_month_explained():
    """月份越界走的是「取值超出范围」分支（报错里带 月 1-12）。"""
    with pytest.raises(CronExpressionError) as exc:
        parse_cron("0 0 * 13 *", "UTC")
    assert "1-12" in str(exc.value)


def test_unknown_timezone_rejected():
    with pytest.raises(CronExpressionError):
        parse_cron("0 9 * * *", "Mars/Olympus")


def test_weekdays_reflect_expression():
    cron = parse_cron("0 9 * * 1-5", "UTC")
    assert cron_weekdays(cron) == {1, 2, 3, 4, 5}


def test_next_after_returns_future_time():
    cron = parse_cron("0 9 * * *", "UTC")
    base = datetime(2026, 11, 19, 10, 0, tzinfo=UTC)

    nxt = next_after(cron, base)

    assert nxt > base
    assert nxt.hour == 9
    assert nxt.day == 20


def test_count_between_closed_interval():
    cron = parse_cron("*/15 * * * *", "UTC")
    start = datetime(2026, 11, 19, 9, 0, tzinfo=UTC)
    end = datetime(2026, 11, 19, 9, 45, tzinfo=UTC)

    # 09:00, 09:15, 09:30, 09:45 —— 闭区间含两端
    assert count_between(cron, start, end) == 4


def test_count_between_start_not_matching_expression():
    """start 本身不符合表达式时不能硬算一次（否则会谎报"错过了一次"）。"""
    cron = parse_cron("0 9 * * *", "UTC")
    start = datetime(2026, 11, 19, 6, 0, tzinfo=UTC)
    end = datetime(2026, 11, 19, 8, 0, tzinfo=UTC)

    assert count_between(cron, start, end) == 0


def test_count_between_rejects_naive_datetimes():
    cron = parse_cron("0 9 * * *", "UTC")
    with pytest.raises(CronExpressionError):
        count_between(cron, datetime(2026, 11, 19, 9, 0), datetime(2026, 11, 20, 9, 0))


def test_count_between_inverted_range_is_zero():
    cron = parse_cron("0 9 * * *", "UTC")
    start = datetime(2026, 11, 20, 9, 0, tzinfo=UTC)
    end = start - timedelta(hours=1)

    assert count_between(cron, start, end) == 0


def test_count_between_iteration_guard():
    """窗口内触发次数超过上限时报错，而不是死循环把 worker 卡住。"""
    cron = parse_cron("* * * * *", "UTC")
    start = datetime(2026, 11, 19, 0, 0, tzinfo=UTC)
    end = start + timedelta(days=2)

    with pytest.raises(CronExpressionError):
        count_between(cron, start, end, max_iterations=10)


def test_validate_cron_returns_none_for_valid():
    now = datetime(2026, 11, 19, 10, 0, tzinfo=UTC)
    assert validate_cron("0 9 * * *", "UTC", now) is None


def test_validate_cron_returns_message_for_bad_expression():
    now = datetime(2026, 11, 19, 10, 0, tzinfo=UTC)
    message = validate_cron("not a cron", "UTC", now)

    assert message is not None
    assert "5 个字段" in message


# ---------- 015 覆盖率补齐：剩余边界分支 ----------


def test_empty_timezone_rejected():
    """`_resolve_tz` 空时区要单独报「不能为空」，而不是「未知时区」。"""
    with pytest.raises(CronExpressionError, match="时区不能为空"):
        parse_cron("0 9 * * *", "   ")


def test_timezone_whitespace_is_stripped():
    cron = parse_cron("0 9 * * *", "  UTC  ")
    assert cron_weekdays(cron)  # 能解析即说明 strip 生效


def test_next_after_rejects_naive_datetime():
    """naive datetime 会被隐式当本地时间——跨部署（容器 UTC）会整体漂移。"""
    cron = parse_cron("0 9 * * *", "UTC")
    with pytest.raises(CronExpressionError, match="带时区"):
        next_after(cron, datetime(2026, 11, 19, 9, 0))


def test_validate_cron_returns_message_when_parse_ok_but_no_next_fire():
    """解析通过但算不出下一次触发（极端情形）→ 同样要返回错误说明。

    `0 0 29 2 *`（2 月 29 日）在连续平年里要等 7 年才轮到下一个闰年，
    超过 4 年的搜索上限——这正是「能解析、但以当前时刻为基准算不出下一次」
    的兜底分支，同时覆盖 next_after 的「可预见时间内不会触发」。
    """
    now = datetime(2097, 3, 1, tzinfo=UTC)
    message = validate_cron("0 0 29 2 *", "UTC", now)
    assert message is not None
    assert "不会产生任何触发时刻" in message


def test_next_after_rejects_beyond_search_horizon():
    """下一次触发超过 4 年上限：宁报错也不要返回一个远处的假时刻。"""
    cron = parse_cron("0 0 29 2 *", "UTC")
    base = datetime(2097, 3, 1, tzinfo=UTC)
    with pytest.raises(CronExpressionError, match="不会触发"):
        next_after(cron, base)
