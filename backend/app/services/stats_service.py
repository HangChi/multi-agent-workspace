"""统计服务（T078）：仪表盘聚合查询（按用户隔离）。

对齐 tasks.md T078：
- running / queued：当前非终态计数
- completed_today / failed_today：今日终态计数
- success_rate_7d：近 7 日成功率 = completed / (completed + failed)
- cost_today_cents：今日成本（非 cancelled）
- cost_trend_7d：近 7 日每日成本序列（[d6, d5, ..., d0]，今日在最后）

实现：单次查询 runs 全部行（按用户 + 状态 + 时间窗口），Python 侧聚合。
数据量受「每用户配额」约束，单用户 runs 行数有限，无需复杂 SQL 窗口。
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.run import Run

_TERMINAL_SUCCESS = {"completed"}
_TERMINAL_FAIL = {"failed", "timeout"}
_NON_TERMINAL = {"queued", "running", "needs_input"}


def _day_start(dt: datetime) -> datetime:
    """UTC 自然日零点。"""
    return dt.replace(hour=0, minute=0, second=0, microsecond=0)


async def overview(db: AsyncSession, owner_id: str) -> dict:
    """用户仪表盘聚合。

    Returns:
        dict: running/queued/completed_today/failed_today/success_rate_7d/
              cost_today_cents/cost_trend_7d
    """
    now = datetime.now(UTC)
    today_start = _day_start(now)
    week_ago = today_start - timedelta(days=6)  # 含今日共 7 天

    # 非终态计数不受时间窗口限制：一个跑了十几天的任务依然是 running，
    # 若按 7 天窗口过滤会从仪表盘「消失」（用户以为任务丢了）。
    active_rows = (
        await db.execute(
            select(Run.status).where(
                Run.owner_id == owner_id,
                Run.status.in_(_NON_TERMINAL),
            )
        )
    ).scalars().all()
    running = sum(1 for s in active_rows if s == "running")
    queued = sum(1 for s in active_rows if s == "queued")

    rows = (
        (
            await db.execute(
                select(Run).where(
                    Run.owner_id == owner_id,
                    Run.created_at >= week_ago,
                )
            )
        )
        .scalars()
        .all()
    )

    completed_today = failed_today = 0
    cost_today = 0
    success_7d = fail_7d = 0

    # 近 7 日成本序列（key: UTC 日期字符串 YYYY-MM-DD → cost）
    trend: dict[str, int] = {}
    for i in range(7):
        day = (week_ago + timedelta(days=i)).date()
        trend[day.isoformat()] = 0

    for run in rows:
        created = run.created_at
        if created < week_ago:
            continue

        if run.status in _TERMINAL_SUCCESS:
            success_7d += 1
        elif run.status in _TERMINAL_FAIL:
            fail_7d += 1
        # cancelled / needs_input / 非终态不计入成功率与今日终态

        if run.status != "cancelled":
            trend[created.date().isoformat()] += run.cost_cents
            if created >= today_start:
                cost_today += run.cost_cents

        if created >= today_start:
            if run.status in _TERMINAL_SUCCESS:
                completed_today += 1
            elif run.status in _TERMINAL_FAIL:
                failed_today += 1

    total_7d = success_7d + fail_7d
    success_rate = round(success_7d / total_7d, 4) if total_7d else 0.0

    return {
        "running": running,
        "queued": queued,
        "completed_today": completed_today,
        "failed_today": failed_today,
        "success_rate_7d": success_rate,
        "cost_today_cents": cost_today,
        "cost_trend_7d": [trend[(week_ago + timedelta(days=i)).date().isoformat()] for i in range(7)],
    }
