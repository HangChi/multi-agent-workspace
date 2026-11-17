"""Public failure and partial-artifact results must survive executor boundaries."""

import pytest

from app.executors.sandbox import RunPythonExecutor
from tests.sandbox_fixtures import FakeNetworkCapture


@pytest.mark.parametrize("exit_code", [0, 1, 2])
def test_denied_network_attempt_is_not_reported_as_plain_exit_failure(
    fake_docker, monkeypatch, exit_code
):
    fake_docker.exit_code = exit_code
    monkeypatch.setattr(FakeNetworkCapture, "collect", lambda _self: ([], True))
    result = RunPythonExecutor()._execute_sync("network request", 5)
    assert result["ok"] is False
    assert result["error_category"] == result["status"] == "policy"
    assert result["policy_denied"] is True
    assert "策略" in result["message"]
    assert result["exit_code"] == exit_code


def test_policy_evidence_does_not_override_cancellation(fake_docker, monkeypatch):
    monkeypatch.setattr(FakeNetworkCapture, "collect", lambda _self: ([], True))
    result = RunPythonExecutor(abort_check=lambda: True)._execute_sync("pass", 5)
    assert result["error_category"] == "cancelled"
    assert result["policy_denied"] is True


@pytest.mark.asyncio
async def test_artifact_truncation_and_execution_id_are_public(monkeypatch):
    from app.services import sandbox_service

    monkeypatch.setattr(
        sandbox_service, "acquire_concurrency_slot", lambda: "test:slot"
    )
    monkeypatch.setattr(sandbox_service, "release_concurrency_slot", lambda _slot: None)
    executor = RunPythonExecutor()
    execution_id, _ = executor.prepare_execution({"code": "pass"})
    truncation = {"reason": "files", "message": "产物文件数超过上限 50"}
    monkeypatch.setattr(
        executor,
        "_execute_sync",
        lambda *_: {
            "ok": True,
            "status": "ok",
            "stdout": "",
            "stderr": "",
            "_sandbox_artifacts": [],
            "_sandbox_artifact_truncated": truncation,
        },
    )
    result = await executor.execute({"code": "pass"})
    assert result["execution_id"] == execution_id
    assert result["artifact_truncated"] == truncation
    assert result["_sandbox_payload"]["artifact_truncated"] == truncation
