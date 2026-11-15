"""沙箱执行事实创建、结束写回与仪表盘指标聚合测试。"""

from __future__ import annotations

import hashlib
from typing import Any

import pytest

from app.models.audit_log import AuditLog
from app.services.sandbox_service import finish_execution, start_execution
from app.services.stats_service import overview


class ScalarResult:
    def __init__(self, values: list[Any]) -> None:
        self.values = values

    def all(self) -> list[Any]:
        return self.values


class FakeResult:
    def __init__(self, values: list[Any]) -> None:
        self._scalars = ScalarResult(values)

    def scalars(self) -> ScalarResult:
        return self._scalars

    def mappings(self) -> FakeResult:
        return self

    def one(self) -> dict[str, Any]:
        return self._scalars.values[0]


class FakeSession:
    def __init__(self, responses: list[list[Any]] | None = None) -> None:
        self.responses = iter(responses or [])
        self.statements = []
        self.added = []
        self.flush_count = 0

    def add(self, value: Any) -> None:
        self.added.append(value)

    async def flush(self) -> None:
        self.flush_count += 1

    async def execute(self, statement):
        self.statements.append(statement)
        return FakeResult(next(self.responses))


def _aggregate(**values: int) -> dict[str, int]:
    return {
        'running': 0, 'queued': 0, 'completed_today': 0, 'failed_today': 0,
        'success_7d': 0, 'fail_7d': 0, 'cost_today_cents': 0,
        'sandbox_runs_today': 0, 'sandbox_duration_ms': 0,
        **{f'cost_day_{i}': 0 for i in range(7)},
        **values,
    }


@pytest.mark.asyncio
async def test_start_and_finish_execution_store_payload_and_audit():
    db = FakeSession()
    code = "print('persist me')"
    execution = await start_execution(
        db,
        run_id="run-id",
        step_id="step-id",
        agent_id="agent-id",
        language="python",
        code=code,
        limits={"timeout_s": 30, "cpu": 1.0},
        execution_id="execution-id",
    )
    assert execution in db.added
    assert execution.status == "running"
    assert execution.code_sha256 == hashlib.sha256(code.encode()).hexdigest()

    completed = await finish_execution(
        db,
        execution,
        {
            "status": "ok",
            "exit_code": 0,
            "stdout": "done\n",
            "stderr": "",
            "stdout_truncated": True,
            "stdout_full_bytes": 99,
            "duration_ms": 123,
            "cpu_ms": 88,
            "mem_peak_bytes": 4096,
        },
        user_id="owner-id",
    )

    assert completed is execution
    assert execution.status == "ok"
    assert execution.exit_code == 0
    assert execution.stdout == "done\n"
    assert execution.stdout_truncated is True
    assert execution.stdout_full_bytes == 99
    assert execution.duration_ms == 123
    assert execution.cpu_ms == 88
    assert execution.mem_peak_bytes == 4096
    assert execution.finished_at is not None
    audit = next(row for row in db.added if isinstance(row, AuditLog))
    assert audit.action == "sandbox_exec"
    assert audit.target_id == "execution-id"
    assert audit.detail == {
        "language": "python",
        "status": "ok",
        "exit_code": 0,
        "duration_ms": 123,
    }


@pytest.mark.asyncio
async def test_overview_aggregates_sandbox_runs_and_minutes_for_owner():
    db = FakeSession([[_aggregate(
        running=2, queued=1, completed_today=1, failed_today=1,
        success_7d=1, fail_7d=1, cost_today_cents=30, cost_day_6=30,
        sandbox_runs_today=2, sandbox_duration_ms=120_000,
    )]])

    result = await overview(db, "owner-id")

    assert result["running"] == 2
    assert result["queued"] == 1
    assert result["completed_today"] == 1
    assert result["failed_today"] == 1
    assert result["success_rate_7d"] == 0.5
    assert result["cost_today_cents"] == 30
    assert result["sandbox_runs_today"] == 2
    assert result["sandbox_minutes_today"] == 2.0
    assert len(result["cost_trend_7d"]) == 7
    assert result["cost_trend_7d"][-1] == 30
    assert len(db.statements) == 1


@pytest.mark.asyncio
async def test_overview_empty_sandbox_data_returns_zero_metrics():
    db = FakeSession([[_aggregate()]])

    result = await overview(db, "another-owner")

    assert result["sandbox_runs_today"] == 0
    assert result["sandbox_minutes_today"] == 0
    assert result["success_rate_7d"] == 0.0
    assert result["cost_today_cents"] == 0
    assert result["cost_trend_7d"] == [0] * 7
