"""沙箱执行器契约测试。"""

import pytest

from app.executors.sandbox import RunPythonExecutor, RunShellExecutor


def test_sandbox_schemas_have_expected_shape() -> None:
    for executor, name, arg in ((RunPythonExecutor(), "run_python", "code"), (RunShellExecutor(), "run_shell", "command")):
        schema = executor.schema()
        assert schema["type"] == "function"
        assert schema["function"]["name"] == name
        assert arg in schema["function"]["parameters"]["properties"]
        assert arg in schema["function"]["parameters"]["required"]


@pytest.mark.asyncio
async def test_missing_argument_returns_failure_not_exception() -> None:
    result = await RunPythonExecutor().execute({})
    assert result["ok"] is False
    assert result["status"] == "failed"
    assert "exit_code" in result and "stdout" in result and "stderr" in result


@pytest.mark.asyncio
async def test_infrastructure_failure_returns_failure(monkeypatch) -> None:
    executor = RunPythonExecutor()

    def fail(*args, **kwargs):
        raise RuntimeError("docker unavailable")

    monkeypatch.setattr(executor, "_execute_sync", fail)
    result = await executor.execute({"code": "print(1)"})
    assert result["ok"] is False
    assert result["status"] == "infra"
    assert result["_sandbox_payload"]["code"] == "print(1)"


@pytest.mark.asyncio
async def test_executor_keeps_sandbox_metadata_outside_model_result(monkeypatch) -> None:
    from app.services import sandbox_service

    executor = RunPythonExecutor()
    execution_id, limits = executor.prepare_execution()
    monkeypatch.setattr(sandbox_service, "acquire_concurrency_slot", lambda: "slot-0:t")
    monkeypatch.setattr(sandbox_service, "release_concurrency_slot", lambda _slot: None)
    monkeypatch.setattr(
        executor,
        "_execute_sync",
        lambda *_args: {
            "ok": True,
            "status": "ok",
            "stdout": "ready",
            "_sandbox_artifacts": [{"name": "result.csv", "size": 6, "data": b"a,b\n1,2"}],
            "_sandbox_artifact_truncated": None,
        },
    )

    result = await executor.execute({"code": "print('ready')"})

    assert result["stdout"] == "ready"
    assert result["_sandbox_payload"] == {
        "execution_id": execution_id,
        "language": "python",
        "code": "print('ready')",
        "limits": limits,
        "artifacts": [{"name": "result.csv", "size": 6, "data": b"a,b\n1,2"}],
        "artifact_truncated": None,
    }
    assert "_sandbox_artifacts" not in result
