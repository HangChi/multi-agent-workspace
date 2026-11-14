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
