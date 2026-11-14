"""Sandbox per-invocation identity, budgets, and lease cleanup regressions."""

from unittest.mock import AsyncMock

import pytest

from app.core.config import settings
from app.executors.sandbox import RunPythonExecutor
from app.services import sandbox_service


def test_prepare_limits_use_default_not_entire_run_budget() -> None:
    executor = RunPythonExecutor(timeout_cap=1800)
    _execution_id, limits = executor.prepare_execution({"code": "print(1)"})
    assert limits["timeout_s"] == settings.sbx_timeout_default


def test_prepare_limits_honor_requested_timeout_and_run_remainder() -> None:
    executor = RunPythonExecutor(timeout_cap=15)
    first, limits = executor.prepare_execution({"code": "print(1)", "timeout_s": 300})
    second, _ = executor.prepare_execution({"code": "print(2)"})
    assert limits["timeout_s"] == 15
    assert first != second


async def test_busy_acquisition_retries_three_times_then_records_failure(monkeypatch) -> None:
    sleep = AsyncMock()
    attempts = []

    def acquire():
        attempts.append(1)

    monkeypatch.setattr(sandbox_service, "acquire_concurrency_slot", acquire)
    monkeypatch.setattr("app.executors.sandbox.asyncio.sleep", sleep)
    executor = RunPythonExecutor()
    executor.prepare_execution({"code": "print(1)"})
    result = await executor.execute({"code": "print(1)"})
    assert result["ok"] is False
    assert result["status"] == "resource"
    assert result["error_category"] == "resource_busy"
    assert "沙箱繁忙" in result["message"]
    assert len(attempts) == 4
    assert [call.args[0] for call in sleep.call_args_list] == [5, 15, 25]
    assert result["_sandbox_payload"]["execution_id"] == executor.execution_id


async def test_infrastructure_failure_releases_owned_lease(monkeypatch) -> None:
    released = []
    monkeypatch.setattr(sandbox_service, "acquire_concurrency_slot", lambda: "0:lease")
    monkeypatch.setattr(sandbox_service, "release_concurrency_slot", released.append)
    executor = RunPythonExecutor()
    executor.prepare_execution({"code": "print(1)"})

    def fail(*_args):
        raise RuntimeError("failed")

    monkeypatch.setattr(executor, "_execute_sync", fail)
    result = await executor.execute({"code": "print(1)"})
    assert result["status"] == "infra"
    assert result["_sandbox_payload"]["code"] == "print(1)"
    assert released == ["0:lease"]


@pytest.mark.parametrize("cap", [0, -1])
async def test_exhausted_run_budget_never_starts_container(cap, monkeypatch) -> None:
    def acquire():
        pytest.fail("An exhausted Run must not acquire a sandbox lease")

    monkeypatch.setattr(sandbox_service, "acquire_concurrency_slot", acquire)
    executor = RunPythonExecutor(timeout_cap=cap)
    result = await executor.execute({"code": "print(1)"})
    assert result["ok"] is False
    assert result["status"] == "timeout"
