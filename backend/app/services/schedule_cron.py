"""周期表达式（cron）的解析与求值 —— 纯函数，无 IO。

为什么单独成模块：调度器最容易出错的地方是「下次什么时候跑」，而这个逻辑
完全可以用纯函数表达。把它和事务/副作用（`schedule_service`）分开，才能用
穷举单测把边界钉死——一旦混进数据库调用，这些边界就测不动了。

## 三个必须知道的坑（都是实测出来的，见 research R-07）

1. **位置参数顺序不是标准 cron 顺序。**
   `celery.schedules.crontab` 的签名是
   `crontab(minute, hour, day_of_week, day_of_month, month_of_year)` ——
   `day_of_week` 在 `day_of_month` **之前**。按标准 cron 的「分 时 日 月 周」
   传位置参数，第 3 个值会被当成星期而不是日期。它不会报错，只会安静地
   按错的日期触发（比如把「每月 1-5 号」解释成「周一到周五」）。
   本模块因此**只使用关键字参数**。

2. **`tz` 不是构造参数。** 传 `crontab(..., tz="Asia/Shanghai")` 会抛
   `TypeError: BaseSchedule.__init__() got an unexpected keyword argument 'tz'`。
   它是类的 `cached_property`，只能对**实例**赋值。赋值后 `remaining_estimate`
   会把 `last_run_at` 和 `now` 都转换到该时区再做匹配。

3. **语义是「日期 ∩ 星期」，不是标准 cron 的并集。**
   `0 9 1 * 1` 在标准 cron 里是「每月 1 号 **或** 每周一」，在这里是
   「既是 1 号**又是**周一」。实测从 2026-11-17 起依次是 2027-02-01、
   2027-03-01、2027-11-01、2028-05-01、2029-01-01 —— 全部同时满足两个条件。
   这是规格 FR-004 要求向用户明示的语义。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from celery.schedules import crontab

# 单次"算错过次数"的迭代上限。按每分钟触发算，525_600 次覆盖整年；
# 超过说明窗口本身不合理（比如时钟被拨到几十年前），此时显式报错比
# 静默截断更好——静默截断会让 missed_count 变成一个看起来正常的假数字。
_MAX_COUNT_ITERATIONS = 1_000_000

# 计算"下一时刻"的硬上限：若表达式在一年内都没有触发点（理论上不该出现，
# 因为 parse 阶段已拒绝永不发生的表达式），必须跳出而不是死循环。
_MAX_NEXT_SEARCH = timedelta(days=366 * 4)


class CronExpressionError(ValueError):
    """周期表达式无法解析、无法求值，或输入不合法。

    消息必须是可直接展示给用户的中文短句：不含类名、模块路径、堆栈片段。
    """


def _resolve_tz(timezone: str) -> ZoneInfo:
    if not timezone or not timezone.strip():
        raise CronExpressionError("时区不能为空")
    try:
        return ZoneInfo(timezone.strip())
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise CronExpressionError(f"未知时区：{timezone}") from exc


def _split_fields(expr: str) -> list[str]:
    """按 5 字段标准形式切分，并给出可读的字段数错误。"""
    if expr is None:
        raise CronExpressionError("周期表达式不能为空")
    fields = expr.split()
    if len(fields) != 5:
        raise CronExpressionError(
            "周期表达式必须是 5 个字段（分 时 日 月 周），"
            f"当前是 {len(fields)} 个：" + (expr.strip() or "（空）")
        )
    return fields


def parse_cron(expr: str, timezone: str) -> crontab:
    """解析 5 字段周期表达式，返回可求值的对象。

    Raises:
        CronExpressionError: 字段数不对、取值越界、日期永不发生、时区未知。
    """
    minute, hour, day_of_month, month_of_year, day_of_week = _split_fields(expr)
    tz = _resolve_tz(timezone)

    try:
        # 必须用关键字参数：见模块文档「坑 1」。
        cron = crontab(
            minute=minute,
            hour=hour,
            day_of_week=day_of_week,
            day_of_month=day_of_month,
            month_of_year=month_of_year,
        )
    except (ValueError, KeyError, TypeError) as exc:
        raise CronExpressionError(_translate_parse_error(exc)) from exc

    # 坑 2：tz 只能实例赋值。
    cron.tz = tz
    return cron


def _translate_parse_error(exc: Exception) -> str:
    """把框架的英文技术报错翻成可直接展示的中文。

    原始消息里带 `crontab` 这类框架名与字段英文名，既暴露实现细节，
    对用户也没有帮助。这里按语义归类成四种，保留"哪个约束没满足"的信息。
    """
    raw = str(exc)
    if "never occurs" in raw:
        # 例：day_of_month '31' never occurs in month_of_year '2'
        return "该周期表达式不会产生任何触发时刻（指定的日期在该月份不存在，例如 2 月 31 日）"
    if "Invalid end range" in raw or "Invalid range" in raw:
        return "周期表达式的字段取值超出范围（分钟 0-59、小时 0-23、日 1-31、月 1-12、周 0-6）"
    if "Invalid weekday literal" in raw or "Invalid day" in raw:
        return "周期表达式的星期字段不合法（取值为 0-6 或名称，0 表示周日）"
    if "Invalid month" in raw:
        return "周期表达式的月份字段不合法（取值为 1-12）"
    return "周期表达式不合法，请检查各字段的取值与格式"


def cron_weekdays(cron: crontab) -> set[int]:
    """返回该表达式允许的星期集合（0=周日，与 cron 一致）。测试与校验用。"""
    return set(cron.day_of_week)


def next_after(cron: crontab, after: datetime) -> datetime:
    """严格晚于 `after` 的下一个触发时刻（带时区）。

    为什么"严格大于"很重要：`remaining_estimate` 在 `after` **正好等于**
    某触发时刻时返回的是下一个触发时刻，因此若调用方把一个已处理的时刻
    再传进来，会正确地跳过它，而不会原地返回同一时刻造成重复触发。

    Raises:
        CronExpressionError: `after` 是 naive datetime，或表达式在可搜索范围内无触发点。
    """
    if after.tzinfo is None:
        # naive datetime 会被隐式当成本地时间，跨部署（容器是 UTC）会整体漂移。
        raise CronExpressionError("基准时刻必须带时区")

    # 用闭包固定"当前时刻"，避免 remaining_estimate 依赖真实 wall clock——
    # 否则无法在测试里验证任意时刻的行为，也无法在时钟异常后复现问题。
    cron.nowfun = lambda: after
    try:
        delta = cron.remaining_estimate(after)
    except (ValueError, OverflowError) as exc:
        raise CronExpressionError(f"无法计算下次触发时间：{exc}") from exc

    nxt = after + delta
    if delta <= timedelta(0):
        # 理论上不该发生（remaining_estimate 总为正）；若发生说明框架行为变了，
        # 宁可报错也不要返回同一时刻 —— 后者会让扫描任务在同一分钟反复触发。
        raise CronExpressionError("无法计算下次触发时间：结果不晚于基准时刻")
    if delta > _MAX_NEXT_SEARCH:
        raise CronExpressionError("该表达式在可预见的时间内不会触发")
    return nxt


def count_between(
    cron: crontab,
    start: datetime,
    end: datetime,
    *,
    max_iterations: int = _MAX_COUNT_ITERATIONS,
) -> int:
    """统计闭区间 `[start, end]` 内的**真实触发时刻**次数。

    闭区间保留 `start`：调用方传入的 start 往往就是那个被错过的计划时刻，
    它确实该跑一次、也确实没跑成，所以要计入。

    但 `start` 只有**本身符合该周期表达式**时才计入。若不符合（例如查询
    「每天 9 点」规则在 06:00–08:00 之间的次数），闭区间语义不意味着
    "把 start 硬算一次"，此时结果是 0 —— 否则会把一个不符合表达式的时刻
    谎报成"错过了一次"。

    Raises:
        CronExpressionError: 参数不合法，或迭代次数超过上限。
    """
    if start.tzinfo is None or end.tzinfo is None:
        raise CronExpressionError("时间范围必须带时区")
    if end < start:
        return 0

    # 先判断 start 自身是否为一次触发：取「严格晚于 start 的上一刻」再推一次，
    # 若恰好得到 start，说明 start 命中表达式。
    count = 0
    cursor = start
    if next_after(cron, cursor - timedelta(seconds=1)) == cursor:
        count = 1

    while True:
        if count > max_iterations:
            raise CronExpressionError(
                "错过的时间窗口过长，无法统计次数（请检查系统时钟）"
            )
        nxt = next_after(cron, cursor)
        if nxt > end:
            break
        count += 1
        cursor = nxt
    return count


def validate_cron(expr: str, timezone: str, now: datetime) -> str | None:
    """校验表达式可用性，返回可直接展示给用户的错误文案（可用则 None）。

    判据是**能算出下一次触发时刻**，而不仅仅是"能解析"：
    `parse_cron` 已拒绝永久不发生的表达式，这里再求一次是为了覆盖
    「解析通过、但以当前时刻为基准算不出下一次」的极端情形。
    """
    try:
        cron = parse_cron(expr, timezone)
    except CronExpressionError as exc:
        return str(exc)

    try:
        next_after(cron, now)
    except CronExpressionError as exc:
        return f"该周期表达式不会产生任何触发时刻：{exc}"
    return None


# 供前端展示的语义说明（FR-004 要求"在用户填写处展示该语义"）。
# 放在后端而不是前端硬编码，是为了让语义说明与实际实现永远同源。
CRON_SEMANTICS_HINT = (
    "5 个字段依次为：分 时 日 月 周。"
    "当「日」和「周」同时填写时，两个条件都要满足才会触发"
    "（例如 0 9 1 * 1 = 既是每月 1 号又是周一，与部分 cron 工具的"
    "「满足其一」不同）。最小粒度为 1 分钟。"
)
