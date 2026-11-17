"""制品回收边界测试。"""

import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.executors.sandbox_client import SandboxOutputLimit
from app.services import sandbox_service


def test_duplicate_names_are_suffixed() -> None:
    used: set[str] = set()
    assert sandbox_service._unique_filename("result.csv", used) == "result.csv"
    used.add("result.csv")
    assert sandbox_service._unique_filename("result.csv", used) == "result-2.csv"
    used.add("result-2.csv")
    assert sandbox_service._unique_filename("result.csv", used) == "result-3.csv"


def test_subdirectory_names_cannot_overwrite_the_same_object_basename() -> None:
    from app.services.artifact_service import build_artifact_key

    used: set[str] = set()
    allocated = []
    for name in ("a/result.csv", "b/result.csv", "result.csv"):
        filename = sandbox_service._unique_filename(name, used)
        used.add(filename)
        allocated.append(filename)
    keys = [build_artifact_key("run", "execution", name) for name in allocated]
    assert len(set(keys)) == 3
    assert allocated == ["a/result.csv", "b/result-2.csv", "result-3.csv"]


def test_binary_mime_fallback() -> None:
    assert sandbox_service.mimetypes.guess_type("data.unknown-ext")[0] is None


@pytest.mark.parametrize("reason", ["files", "file_size", "total_size"])
def test_output_limit_is_a_structured_error(reason: str) -> None:
    error = SandboxOutputLimit(reason, "超过上限")
    assert error.reason == reason
    assert str(error) == "超过上限"


def test_sha256_is_content_based() -> None:
    assert sandbox_service._sha256_text("hello") == hashlib.sha256(b"hello").hexdigest()


@pytest.mark.parametrize("reason", ["files", "file_size", "total_size"])
def test_artifact_truncation_projection_matches_execution(reason):
    warning = {"reason": reason, "message": "超过上限"}
    output = {"execution_id": "exec-1", "artifact_truncated": warning}
    assert sandbox_service.execution_artifact_truncation(output, "exec-1") == warning
    assert sandbox_service.execution_artifact_truncation(output, "exec-2") is None


@pytest.mark.parametrize(
    "output",
    [
        None,
        [],
        "legacy",
        {},
        {"artifact_truncated": {"reason": "files", "message": "missing execution"}},
        {"execution_id": "exec-1"},
        {"execution_id": "exec-1", "artifact_truncated": None},
        {"execution_id": "exec-1", "artifact_truncated": True},
        {"execution_id": "exec-1", "artifact_truncated": {"reason": "files"}},
        {
            "execution_id": "exec-1",
            "artifact_truncated": {"reason": [], "message": "bad"},
        },
    ],
)
def test_artifact_truncation_projection_ignores_missing_or_malformed_values(output):
    assert sandbox_service.execution_artifact_truncation(output, "exec-1") is None


@pytest.mark.parametrize("source", ["payload", "stream"])
async def test_artifact_persistence_keeps_names_mime_sizes_and_linkage(
    monkeypatch, source
):
    db = SimpleNamespace(
        add=Mock(), flush=AsyncMock(), get=AsyncMock(return_value=None)
    )
    put = Mock(return_value=("object-key", "sha"))
    monkeypatch.setattr(sandbox_service.artifact_service, "put_bytes", put)
    execution = SimpleNamespace(id="exec-1")
    monkeypatch.setattr(
        sandbox_service, "start_execution", AsyncMock(return_value=execution)
    )
    monkeypatch.setattr(sandbox_service, "finish_execution", AsyncMock())
    files = [
        ("result.csv", b"a,b"),
        ("result.csv", b"c,d"),
        ("binary.unknown-ext", b"x"),
    ]

    if source == "payload":
        await sandbox_service.persist_sandbox_result(
            db,
            run_id="run-1",
            step_id="step-1",
            agent_id="agent-1",
            user_id="user-1",
            payload={
                "artifacts": [
                    {"name": name, "data": data, "size": len(data)}
                    for name, data in files
                ]
            },
            result={"status": "ok"},
        )
    else:
        sandbox = SimpleNamespace(
            iter_output_files=lambda **kwargs: (
                SimpleNamespace(name=name, data=data, size=len(data))
                for name, data in files
            )
        )
        artifacts = await sandbox_service.collect_artifacts(
            db,
            sandbox,
            run_id="run-1",
            execution_id="exec-1",
            step_id="step-1",
        )
        assert len(artifacts) == 3

    rows = [call.args[0] for call in db.add.call_args_list]
    assert [row.filename for row in rows] == [
        "result.csv",
        "result-2.csv",
        "binary.unknown-ext",
    ]
    assert [row.size_bytes for row in rows] == [3, 3, 1]
    assert rows[-1].mime_type == "application/octet-stream"
    assert all(
        row.run_id == "run-1" and row.step_id == "step-1" and row.sha256 == "sha"
        for row in rows
    )
    assert [call.args[:3] for call in put.call_args_list] == [
        ("run-1", "exec-1", row.filename) for row in rows
    ]


@pytest.mark.parametrize("reason", ["files", "file_size", "total_size"])
async def test_collect_artifacts_preserves_partial_output_and_truncation_audit(
    monkeypatch, reason
):
    db = SimpleNamespace(add=Mock(), flush=AsyncMock())
    monkeypatch.setattr(
        sandbox_service.artifact_service, "put_bytes", Mock(return_value=("key", "sha"))
    )
    audit = AsyncMock()
    monkeypatch.setattr(sandbox_service, "write_audit", audit)

    def files(**kwargs):
        yield SimpleNamespace(name="first.txt", data=b"ok", size=2)
        raise SandboxOutputLimit(reason, "超过上限")

    rows = await sandbox_service.collect_artifacts(
        db,
        SimpleNamespace(iter_output_files=files),
        run_id="run-1",
        execution_id="exec-1",
        step_id="step-1",
        user_id="user-1",
    )
    assert [row.filename for row in rows] == ["first.txt"]
    assert audit.await_args.kwargs["detail"] == {
        "reason": reason,
        "message": "超过上限",
    }
    assert audit.await_args.kwargs["target_id"] == "exec-1"
