"""Dashboard query shape and SQL behavior without external DBs or pytest fixtures."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from sqlalchemy import (
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    Uuid,
    create_engine,
)
from sqlalchemy.dialects import postgresql

from app.services.stats_service import overview

OWNER = "11111111-1111-1111-1111-111111111111"
OTHER = "22222222-2222-2222-2222-222222222222"
NOW = datetime(2026, 11, 15, 0, 5, tzinfo=UTC)
TODAY = NOW.replace(hour=0, minute=0)
WEEK = TODAY - timedelta(days=6)


class FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)


def empty_aggregate():
    return {
        "running": 0,
        "queued": 0,
        "completed_today": 0,
        "failed_today": 0,
        "success_7d": 0,
        "fail_7d": 0,
        "cost_today_cents": 0,
        "sandbox_runs_today": 0,
        "sandbox_duration_ms": 0,
        **{f"cost_day_{i}": 0 for i in range(7)},
    }


class RecordingSession:
    def __init__(self, connection=None):
        self.connection = connection
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        if self.connection is not None:
            return self.connection.execute(statement)
        # Allow the pre-optimization implementation to complete too, so its
        # regression fails on unbounded query shape rather than a mock error.
        return SimpleNamespace(
            scalars=lambda: SimpleNamespace(all=list),
            mappings=lambda: SimpleNamespace(one=empty_aggregate),
        )


class StatsAggregationTests(IsolatedAsyncioTestCase):
    async def test_one_owner_scoped_query_returns_only_database_aggregates(self):
        db = RecordingSession()
        with patch("app.services.stats_service.datetime", FrozenDatetime):
            result = await overview(db, OWNER)
        self.assertEqual(
            len(db.statements), 1, "dashboard must not fetch per-run/per-execution rows"
        )
        compiled = db.statements[0].compile(dialect=postgresql.dialect())
        sql = str(compiled).lower()
        self.assertIn("count(", sql)
        self.assertIn("sum(", sql)
        self.assertIn("runs.owner_id =", sql)
        self.assertIn("agents.owner_id =", sql)
        for payload_column in (
            "runs.result",
            "runs.tool_grants_snapshot",
            "sandbox_executions.code",
            "sandbox_executions.stdout",
            "sandbox_executions.stderr",
        ):
            self.assertNotIn(payload_column, sql)
        dates = [
            value for value in compiled.params.values() if isinstance(value, datetime)
        ]
        self.assertTrue(dates)
        self.assertTrue(all(value.tzinfo == UTC for value in dates))
        self.assertIn(WEEK, dates)
        self.assertIn(TODAY, dates)
        self.assertEqual(result["cost_trend_7d"], [0] * 7)

    async def test_sql_preserves_utc_windows_status_rules_owner_scope_and_rounding(
        self,
    ):
        metadata = MetaData()
        runs = Table(
            "runs",
            metadata,
            Column("owner_id", Uuid(as_uuid=False)),
            Column("status", String),
            Column("created_at", DateTime(timezone=True)),
            Column("cost_cents", Integer),
        )
        agents = Table(
            "agents",
            metadata,
            Column("id", Uuid(as_uuid=False)),
            Column("owner_id", Uuid(as_uuid=False)),
        )
        executions = Table(
            "sandbox_executions",
            metadata,
            Column("agent_id", Uuid(as_uuid=False)),
            Column("created_at", DateTime(timezone=True)),
            Column("duration_ms", Integer),
        )
        engine = create_engine("sqlite://")
        metadata.create_all(engine)
        agent_a = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        agent_b = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
        agent_other = "cccccccc-cccc-cccc-cccc-cccccccccccc"
        data = [
            ("running", TODAY - timedelta(days=20), 99),
            ("queued", TODAY - timedelta(days=20), 88),
            ("needs_input", TODAY - timedelta(days=20), 77),
            ("completed", WEEK - timedelta(microseconds=1), 1000),
            ("completed", WEEK, 7),
            ("failed", WEEK + timedelta(days=1, hours=1), 11),
            ("completed", TODAY - timedelta(microseconds=1), 13),
            ("timeout", TODAY - timedelta(hours=1), 17),
            ("cancelled", TODAY - timedelta(hours=1), 100),
            ("completed", TODAY, 23),
            ("failed", NOW, 29),
            ("timeout", NOW, 31),
            ("cancelled", NOW, 200),
            ("running", NOW, 5),
            ("queued", NOW, 3),
            ("needs_input", NOW, 2),
        ]
        try:
            with engine.begin() as connection:
                connection.execute(
                    runs.insert(),
                    [
                        {
                            "owner_id": OWNER,
                            "status": status,
                            "created_at": date,
                            "cost_cents": cost,
                        }
                        for status, date, cost in data
                    ]
                    + [
                        {
                            "owner_id": OTHER,
                            "status": "completed",
                            "created_at": NOW,
                            "cost_cents": 999,
                        }
                    ],
                )
                connection.execute(
                    agents.insert(),
                    [
                        {"id": agent_a, "owner_id": OWNER},
                        {"id": agent_b, "owner_id": OWNER},
                        {"id": agent_other, "owner_id": OTHER},
                    ],
                )
                connection.execute(
                    executions.insert(),
                    [
                        {
                            "agent_id": agent_a,
                            "created_at": TODAY,
                            "duration_ms": 25_010,
                        },
                        {"agent_id": agent_b, "created_at": NOW, "duration_ms": 75_290},
                        {"agent_id": agent_a, "created_at": NOW, "duration_ms": 0},
                        {
                            "agent_id": agent_a,
                            "created_at": TODAY - timedelta(microseconds=1),
                            "duration_ms": 900_000,
                        },
                        {
                            "agent_id": agent_other,
                            "created_at": NOW,
                            "duration_ms": 900_000,
                        },
                    ],
                )
                db = RecordingSession(connection)
                with patch("app.services.stats_service.datetime", FrozenDatetime):
                    result = await overview(db, OWNER)
                self.assertEqual(
                    result,
                    {
                        "running": 2,
                        "queued": 2,
                        "completed_today": 1,
                        "failed_today": 2,
                        "success_rate_7d": round(3 / 7, 4),
                        "cost_today_cents": 93,
                        "cost_trend_7d": [7, 11, 0, 0, 0, 30, 93],
                        "sandbox_runs_today": 3,
                        "sandbox_minutes_today": 1.67,
                    },
                )
                with patch("app.services.stats_service.datetime", FrozenDatetime):
                    empty = await overview(db, "dddddddd-dddd-dddd-dddd-dddddddddddd")
                self.assertEqual(empty["cost_trend_7d"], [0] * 7)
                self.assertEqual(empty["sandbox_runs_today"], 0)
                self.assertEqual(empty["sandbox_minutes_today"], 0)
                self.assertEqual(empty["success_rate_7d"], 0.0)
        finally:
            engine.dispose()

    async def test_non_terminal_scope_includes_retrying(self):
        """R3 回归：`retrying` 必须在「非终态」口径里。

        原 `_NON_TERMINAL = {"queued", "running", "needs_input"}` 漏了
        006 的 `retrying`。它的用处是让**窗口之外但仍未结束**的 Run 也进聚合
        （`or_(this_week, status.in_(...))`）。漏掉后的症状很隐蔽：
        一条三天前创建、正在等重试的 Run 会整个从仪表盘消失，
        用户看到「进行中 0」却看见看板上卡片还在转。

        上面那条等值断言抓不到——`retrying` 漏掉不改变任何数值（只少算了
        一类行，而测试数据里没有它）。所以这里直接查 WHERE 子句里的绑定值。

        反向验证：把 `_NON_TERMINAL` 换回旧字面量，本用例变红。
        """
        db = RecordingSession()
        with patch("app.services.stats_service.datetime", FrozenDatetime):
            await overview(db, OWNER)

        # 必须用 literal_binds 展开：IN (...) 走的是 expanding 参数，
        # `compiled.params` 只会给出一个占位值，看不到集合全貌。
        sql = str(
            db.statements[0].compile(
                dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
            )
        ).lower()

        # 非终态集合出现在 `runs.status IN (...)` 里；确认四个非终态都在。
        for status in ("queued", "running", "needs_input", "retrying"):
            self.assertIn(
                f"'{status}'", sql, f"{status} 必须算非终态（否则窗口外任务漏统计）"
            )

        # 反向保护：终态不该混进非终态 IN（否则窗口过滤失效、退化成扫全表）
        self.assertNotIn("runs.status not in", sql)
