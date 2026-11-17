"""定时调度的周期表达式（cron）纯函数测试。

这些函数不做 IO，因此可以把边界穷举掉 —— 而"下次触发时间算错"恰好是调度器
最容易出、也最难在生产里发现的事故（往往表现为"它就是不按我说的跑"）。

实测背景（写这些断言时的依据，见 specs/004-scheduled-requests/research.md R-07）：
- celery 的 `crontab` 位置参数顺序是 minute, hour, **day_of_week, day_of_month**,
  month_of_year —— 按标准 cron 顺序（分 时 日 月 周）传位置参数会解析错。
- `tz` 不是构造参数（传 `tz=` 抛 TypeError），只能对实例赋值。
- 语义是「日期 ∩ 星期」，与标准 cron 的并集不同。
"""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.services.schedule_cron import (
    CronExpressionError,
    count_between,
    next_after,
    parse_cron,
    validate_cron,
)

SH = ZoneInfo("Asia/Shanghai")


def at(y: int, m: int, d: int, hh: int = 0, mm: int = 0, ss: int = 0) -> datetime:
    """构造 Asia/Shanghai 时区的时刻（测试可读性用）。"""
    return datetime(y, m, d, hh, mm, ss, tzinfo=SH)


class TestParseCron:
    def test_accepts_standard_five_fields(self):
        cron = parse_cron("*/5 * * * *", "Asia/Shanghai")
        assert cron is not None

    def test_weekday_field_is_parsed_from_the_right_position(self):
        """`0 9 * * 1-5` 的第 5 个字段是「星期」，不是「月份」。

        这正是位置参数顺序写反时会错的地方：写反后 1-5 会被当成 day_of_month，
        于是规则变成"每月 1-5 号的 9 点"，与用户写的"工作日"完全不同，
        而且不会报错 —— 只会安静地按错的日期触发。
        """
        from app.services.schedule_cron import cron_weekdays

        assert cron_weekdays(parse_cron("0 9 * * 1-5", "Asia/Shanghai")) == {
            1,
            2,
            3,
            4,
            5,
        }

    def test_weekday_zero_is_sunday(self):
        from app.services.schedule_cron import cron_weekdays

        assert cron_weekdays(parse_cron("0 9 * * 0", "Asia/Shanghai")) == {0}

    @pytest.mark.parametrize(
        "expr",
        [
            "@daily",  # 不支持别名
            "0 9 * * * *",  # 六字段（秒级）不支持，最小粒度为分钟
            "* * * *",  # 字段不足
            "",  # 空
            "   ",  # 空白
        ],
    )
    def test_rejects_unsupported_forms(self, expr: str):
        with pytest.raises(CronExpressionError):
            parse_cron(expr, "Asia/Shanghai")

    @pytest.mark.parametrize(
        "expr",
        [
            "60 * * * *",  # 分钟越界
            "0 24 * * *",  # 小时越界
            "0 9 32 * *",  # 日越界
            "0 9 * 13 *",  # 月越界
        ],
    )
    def test_rejects_out_of_range_values(self, expr: str):
        with pytest.raises(CronExpressionError):
            parse_cron(expr, "Asia/Shanghai")

    def test_rejects_never_occurring_date(self):
        """2 月 31 日永远不会发生 —— 必须拒绝而不是接受后永不触发。

        接受它比拒绝它更糟：用户会以为规则建好了，实际永远等不到。
        """
        with pytest.raises(CronExpressionError):
            parse_cron("0 0 31 2 *", "Asia/Shanghai")

    def test_rejects_unknown_timezone(self):
        with pytest.raises(CronExpressionError):
            parse_cron("0 9 * * *", "Not/AZone")

    def test_rejects_empty_timezone(self):
        with pytest.raises(CronExpressionError):
            parse_cron("0 9 * * *", "")


class TestNextAfter:
    def test_before_the_hour_returns_today(self):
        cron = parse_cron("0 9 * * *", "Asia/Shanghai")
        assert next_after(cron, at(2026, 11, 17, 8, 59)) == at(2026, 11, 17, 9, 0)

    def test_exact_hit_advances_to_next_occurrence(self):
        """正好等于计划时刻时，要推进到**下一次**，否则会重复触发同一时刻。"""
        cron = parse_cron("0 9 * * *", "Asia/Shanghai")
        assert next_after(cron, at(2026, 11, 17, 9, 0)) == at(2026, 11, 18, 9, 0)

    def test_after_the_hour_returns_tomorrow(self):
        cron = parse_cron("0 9 * * *", "Asia/Shanghai")
        assert next_after(cron, at(2026, 11, 17, 9, 1)) == at(2026, 11, 18, 9, 0)

    def test_weekdays_skip_the_weekend(self):
        """周五 18:00 的下一次「工作日 9 点」必须是下周一，不是周六。"""
        cron = parse_cron("0 9 * * 1-5", "Asia/Shanghai")
        assert next_after(cron, at(2026, 11, 20, 18, 0)) == at(2026, 11, 23, 9, 0)

    def test_saturday_returns_monday(self):
        cron = parse_cron("0 9 * * 1-5", "Asia/Shanghai")
        assert next_after(cron, at(2026, 11, 21, 0, 0)) == at(2026, 11, 23, 9, 0)

    def test_leap_year_is_respected(self):
        """2027 不是闰年，2 月 29 日必须跳到 2028。"""
        cron = parse_cron("0 0 29 2 *", "Asia/Shanghai")
        assert next_after(cron, at(2026, 3, 1)) == at(2028, 2, 29)

    def test_date_and_weekday_are_intersected_not_unioned(self):
        """`0 9 1 * 1` = 既是 1 号又是周一。

        标准 cron 在这里是并集（每月 1 号 **或** 每周一），celery 是交集。
        实测交集的序列：2027-02-01, 2027-03-01, 2027-11-01, 2028-05-01, 2029-01-01
        —— 全部既是 1 号又是周一。若实现成并集，下一步就会是 2026-11-23（周一）。
        """
        cron = parse_cron("0 9 1 * 1", "Asia/Shanghai")
        nxt = next_after(cron, at(2026, 11, 17))
        assert nxt == at(2027, 2, 1, 9, 0)
        assert nxt.day == 1 and nxt.weekday() == 0

    def test_every_minute(self):
        cron = parse_cron("* * * * *", "Asia/Shanghai")
        assert next_after(cron, at(2026, 11, 17, 9, 0, 30)) == at(2026, 11, 17, 9, 1)

    def test_result_is_timezone_aware_in_utc_comparable_terms(self):
        """结果必须能安全地与 UTC 的 now 比较（库里存的是 timestamptz）。"""
        cron = parse_cron("0 9 * * *", "Asia/Shanghai")
        nxt = next_after(cron, datetime(2026, 11, 17, 0, 0, tzinfo=UTC))
        assert nxt.astimezone(UTC) == datetime(2026, 11, 17, 1, 0, tzinfo=UTC)


class TestCountBetween:
    def test_two_hours_of_a_five_minute_rule(self):
        """实测值 25：01:00…03:00 之间的 5 分钟整点，含两端。"""
        cron = parse_cron("*/5 * * * *", "Asia/Shanghai")
        assert count_between(cron, at(2026, 11, 17, 1, 0), at(2026, 11, 17, 3, 0)) == 25

    def test_nine_minutes_of_a_five_minute_rule(self):
        cron = parse_cron("*/5 * * * *", "Asia/Shanghai")
        assert count_between(cron, at(2026, 11, 17, 1, 0), at(2026, 11, 17, 1, 9)) == 2

    def test_single_occurrence_when_window_ends_mid_interval(self):
        cron = parse_cron("*/5 * * * *", "Asia/Shanghai")
        assert (
            count_between(cron, at(2026, 11, 17, 1, 0), at(2026, 11, 17, 1, 0, 45)) == 1
        )

    def test_window_before_first_occurrence_is_zero(self):
        cron = parse_cron("0 9 * * *", "Asia/Shanghai")
        assert count_between(cron, at(2026, 11, 17, 6, 0), at(2026, 11, 17, 8, 0)) == 0

    def test_long_window_is_bounded(self):
        """极端窗口（一年每分钟）不能无限循环或耗尽内存。

        数值是 525601 而不是 365×1440=525600：区间**闭**，两端都算。
        2026 不是闰年，365 天整；首尾各 00:00 都被计入，故多 1。
        这条断言同时把「闭区间」这个语义钉住了 —— 它决定了停机恢复时
        missed_count 会不会少算那一次（正好是用户最在意的那一次）。
        """
        cron = parse_cron("* * * * *", "Asia/Shanghai")
        n = count_between(cron, at(2026, 1, 1), at(2027, 1, 1))
        assert n == 525_601

    def test_over_cap_raises_instead_of_silently_truncating(self):
        """超过迭代上限必须显式报错：静默截断会让 missed_count 变成假数字。"""
        cron = parse_cron("* * * * *", "Asia/Shanghai")
        with pytest.raises(CronExpressionError):
            count_between(cron, at(2020, 1, 1), at(2026, 1, 1), max_iterations=1000)


class TestValidateCron:
    def test_returns_none_for_valid_expression(self):
        assert validate_cron("0 9 * * 1-5", "Asia/Shanghai", at(2026, 11, 17)) is None

    def test_returns_chinese_message_for_unparseable_expression(self):
        msg = validate_cron("60 * * * *", "Asia/Shanghai", at(2026, 11, 17))
        assert msg is not None and "周期表达式" in msg

    def test_returns_chinese_message_for_never_occurring(self):
        msg = validate_cron("0 0 31 2 *", "Asia/Shanghai", at(2026, 11, 17))
        assert msg is not None and "不会" in msg

    def test_message_does_not_leak_internal_details(self):
        """错误要能被直接展示给用户：不含类名、模块路径、堆栈片段。"""
        for expr in ("60 * * * *", "0 0 31 2 *", "@daily"):
            msg = validate_cron(expr, "Asia/Shanghai", at(2026, 11, 17))
            assert msg is not None
            for leak in ("ValueError", "Traceback", "celery", "crontab", "app."):
                assert leak not in msg


class TestTimezoneHandling:
    def test_different_timezones_yield_different_instants(self):
        """同一表达式在不同时区必须得到不同的绝对时刻。

        若实现忽略了 tz（比如永远按 UTC 求值），这两个断言会相等 ——
        那意味着用户在 UTC+8 写的"每天 9 点"会变成北京时间 17 点触发。
        """
        sh = next_after(parse_cron("0 9 * * *", "Asia/Shanghai"), at(2026, 11, 17))
        utc = next_after(parse_cron("0 9 * * *", "UTC"), at(2026, 11, 17))
        assert sh.astimezone(UTC) != utc.astimezone(UTC)
        assert sh.astimezone(UTC) == datetime(2026, 11, 17, 1, 0, tzinfo=UTC)
        assert utc.astimezone(UTC) == datetime(2026, 11, 17, 9, 0, tzinfo=UTC)

    def test_naive_datetime_is_rejected(self):
        """naive datetime 会被隐式当成本地时间，跨部署会漂移；必须显式拒绝。"""
        cron = parse_cron("0 9 * * *", "Asia/Shanghai")
        with pytest.raises(CronExpressionError):
            next_after(cron, datetime(2026, 11, 17, 8, 0))


class TestAdvanceDoesNotDrift:
    def test_repeated_advance_matches_direct_computation(self):
        """连续推进 10 次必须落在可预测的时刻（无累积漂移）。

        `*/7` 在每小时的 0,7,14,21,28,35,42,49,56 分触发。从 00:00 起推进 10 次：
        00:07 00:14 00:21 00:28 00:35 00:42 00:49 00:56 01:00 01:07 —— 即 67 分钟。
        注意第 9 次是 01:00 而不是 01:03：56 之后本小时已无符合分钟，必须跳到下一小时。
        """
        cron = parse_cron("*/7 * * * *", "Asia/Shanghai")
        t = at(2026, 11, 17, 0, 0)
        for _ in range(10):
            t = next_after(cron, t)
        assert t.minute % 7 == 0
        assert t - at(2026, 11, 17, 0, 0) == timedelta(minutes=67)
