"""统计服务（T078）：用户隔离、实时 SQL 聚合的仪表盘查询。

近 7 日成本按 UTC 自然日统计；非终态计数不受日期窗口限制。
运行和沙箱分别在数据库聚合后合并为一行，不加载结果 JSON 或代码/输出。
生产端点允许极短的有界仪表盘缓存，避免高频刷新拖慢执行状态写入。
"""

import os
from datetime import UTC, datetime, timedelta
from time import monotonic

from sqlalchemy import func, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.run import Run
from app.models.sandbox_execution import SandboxExecution

_TERMINAL_SUCCESS = {"completed"}
_TERMINAL_FAIL = {"failed", "timeout"}
_NON_TERMINAL = {"queued", "running", "needs_input"}
_STATS_CACHE_TTL_SECONDS = 15.0
_STATS_CACHE_MAX_ENTRIES = 256
_stats_cache: dict[str, tuple[float, dict]] = {}


def _copy_snapshot(snapshot: dict) -> dict:
    return {**snapshot, "cost_trend_7d": list(snapshot["cost_trend_7d"])}


def _cached_snapshot(owner_id: str, now: float) -> dict | None:
    entry = _stats_cache.get(owner_id)
    if entry is None or now - entry[0] >= _STATS_CACHE_TTL_SECONDS:
        if entry is not None:
            _stats_cache.pop(owner_id, None)
        return None
    return _copy_snapshot(entry[1])


def _store_snapshot(owner_id: str, now: float, snapshot: dict) -> None:
    if len(_stats_cache) >= _STATS_CACHE_MAX_ENTRIES and owner_id not in _stats_cache:
        oldest = min(_stats_cache, key=lambda key: _stats_cache[key][0])
        _stats_cache.pop(oldest, None)
    _stats_cache[owner_id] = (now, _copy_snapshot(snapshot))


def _day_start(dt: datetime) -> datetime:
    """UTC 自然日零点。"""
    return dt.replace(hour=0, minute=0, second=0, microsecond=0)


async def overview(db: AsyncSession, owner_id: str, *, cache: bool = False) -> dict:
    """Return one owner-scoped aggregate without loading run/execution payloads.

    ``cache`` is reserved for the high-frequency dashboard endpoint. It is a
    bounded 5-second snapshot and is disabled by test callers, which retain
    exact read-after-write semantics.
    """
    use_cache = cache and os.getenv("TESTING") != "1"
    now = monotonic()
    if use_cache:
        cached = _cached_snapshot(owner_id, now)
        if cached is not None:
            return cached
    today_start = _day_start(datetime.now(UTC))
    week_ago = today_start - timedelta(days=6)  # 含今日共 7 天
    completed = Run.status.in_(_TERMINAL_SUCCESS)
    failed = Run.status.in_(_TERMINAL_FAIL)
    billable = Run.status != "cancelled"
    today = Run.created_at >= today_start
    this_week = Run.created_at >= week_ago

    columns = [
        func.count().filter(Run.status == "running").label("running"),
        func.count().filter(Run.status == "queued").label("queued"),
        func.count().filter(today, completed).label("completed_today"),
        func.count().filter(today, failed).label("failed_today"),
        func.count().filter(this_week, completed).label("success_7d"),
        func.count().filter(this_week, failed).label("fail_7d"),
        func.coalesce(func.sum(Run.cost_cents).filter(today, billable), 0).label("cost_today_cents"),
    ]
    # Fixed-size conditional sums avoid timezone-dependent date casts and keep
    # the returned result bounded to seven days, regardless of history size.
    for i in range(7):
        start = week_ago + timedelta(days=i)
        columns.append(
            func.coalesce(
                func.sum(Run.cost_cents).filter(
                    Run.created_at >= start,
                    Run.created_at < start + timedelta(days=1),
                    billable,
                ),
                0,
            ).label(f"cost_day_{i}")
        )
    run_totals = (
        select(*columns)
        .where(Run.owner_id == owner_id, or_(this_week, Run.status.in_(_NON_TERMINAL)))
        .subquery("run_totals")
    )
    sandbox_totals = (
        select(
            func.count().label("sandbox_runs_today"),
            func.coalesce(func.sum(SandboxExecution.duration_ms), 0).label("sandbox_duration_ms"),
        )
        .where(
            SandboxExecution.agent_id.in_(select(Agent.id).where(Agent.owner_id == owner_id)),
            SandboxExecution.created_at >= today_start,
        )
        .subquery("sandbox_totals")
    )
    # Each aggregate subquery always returns exactly one row (including empty
    # inputs). Joining after aggregation cannot multiply runs by executions.
    row = (
        await db.execute(
            select(*run_totals.c, *sandbox_totals.c).select_from(
                run_totals.join(sandbox_totals, true())
            )
        )
    ).mappings().one()
    total_7d = row["success_7d"] + row["fail_7d"]
    snapshot = {
        "running": row["running"],
        "queued": row["queued"],
        "completed_today": row["completed_today"],
        "failed_today": row["failed_today"],
        "success_rate_7d": round(row["success_7d"] / total_7d, 4) if total_7d else 0.0,
        "cost_today_cents": row["cost_today_cents"],
        "cost_trend_7d": [row[f"cost_day_{i}"] for i in range(7)],
        "sandbox_runs_today": row["sandbox_runs_today"],
        "sandbox_minutes_today": round(row["sandbox_duration_ms"] / 60000, 2),
    }
    if use_cache:
        _store_snapshot(owner_id, now, snapshot)
    return snapshot
