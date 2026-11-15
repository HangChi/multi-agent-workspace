"""002 执行详情 API：摘要与完整输出的边界及归属校验。"""

from __future__ import annotations

import uuid

import pytest

from app.models.artifact import Artifact
from app.models.step import Step

RUNS_URL = "/api/v1/runs"


async def test_run_summary_and_execution_detail_are_split(
    client, make_headers, make_user, sandbox_factory, db_session
):
    owner, _ = await make_user()
    headers = await make_headers(user=owner)
    execution = await sandbox_factory(
        owner=owner,
        status="ok",
        code="print('secret code')",
        exit_code=0,
    )
    execution.stdout = "hello"
    execution.stderr = ""
    execution.stdout_full_bytes = 5
    execution.duration_ms = 42
    execution.finished_at = execution.created_at
    await db_session.commit()

    summary_response = await client.get(f"{RUNS_URL}/{execution.run_id}", headers=headers)
    assert summary_response.status_code == 200, summary_response.text
    body = summary_response.json()
    assert body["executions"] == [
        {
            "id": execution.id,
            "step_seq": 1,
            "language": "python",
            "status": "ok",
            "exit_code": 0,
            "duration_ms": 42,
            "stdout_truncated": False,
            "artifacts": 0,
        }
    ]
    assert "code" not in body["executions"][0]
    assert "stdout" not in body["executions"][0]

    detail_response = await client.get(
        f"{RUNS_URL}/{execution.run_id}/executions/{execution.id}", headers=headers
    )
    assert detail_response.status_code == 200, detail_response.text
    detail = detail_response.json()
    assert detail["code"] == "print('secret code')"
    assert detail["stdout"] == "hello"
    assert detail["code_sha256"] == "0" * 64
    assert detail["step_seq"] == 1


@pytest.mark.parametrize("reason", ["files", "file_size", "total_size"])
async def test_execution_detail_projects_artifact_truncation_from_its_step(
    client, make_headers, make_user, sandbox_factory, db_session, reason
):
    owner, _ = await make_user()
    headers = await make_headers(user=owner)
    execution = await sandbox_factory(owner=owner, status="ok", exit_code=0)
    step = await db_session.get(Step, execution.step_id)
    warning = {"reason": reason, "message": "制品达到限制，后续文件未回收。"}
    step.output = {"execution_id": execution.id, "artifact_truncated": warning}
    await db_session.commit()

    response = await client.get(
        f"{RUNS_URL}/{execution.run_id}/executions/{execution.id}", headers=headers
    )
    assert response.status_code == 200, response.text
    assert response.json()["artifact_truncated"] == warning
    assert response.json()["status"] == "ok"
    assert response.json()["error_message"] is None


@pytest.mark.parametrize("output_kind", ["other_execution", "missing_id", "legacy"])
async def test_execution_detail_does_not_misattribute_artifact_truncation(
    client, make_headers, make_user, sandbox_factory, db_session, output_kind
):
    owner, _ = await make_user()
    headers = await make_headers(user=owner)
    execution = await sandbox_factory(owner=owner, status="ok", exit_code=0)
    step = await db_session.get(Step, execution.step_id)
    output = {"artifact_truncated": {"reason": "files", "message": "different execution"}}
    if output_kind == "other_execution":
        output["execution_id"] = str(uuid.uuid4())
    step.output = None if output_kind == "legacy" else output
    await db_session.commit()

    response = await client.get(
        f"{RUNS_URL}/{execution.run_id}/executions/{execution.id}", headers=headers
    )
    assert response.status_code == 200, response.text
    assert response.json()["artifact_truncated"] is None


async def test_execution_detail_includes_artifact_brief_and_hides_other_owner(
    client, make_headers, make_user, sandbox_factory, db_session
):
    owner, _ = await make_user()
    headers = await make_headers(user=owner)
    execution = await sandbox_factory(owner=owner, status="ok", exit_code=0)
    artifact = Artifact(
        run_id=execution.run_id,
        step_id=execution.step_id,
        object_key=f"artifacts/{execution.run_id}/{execution.id}/result.txt",
        filename="result.txt",
        size_bytes=17,
        mime_type="text/plain",
        sha256="a" * 64,
    )
    db_session.add(artifact)
    await db_session.commit()

    response = await client.get(
        f"{RUNS_URL}/{execution.run_id}/executions/{execution.id}", headers=headers
    )
    assert response.status_code == 200
    assert response.json()["artifacts"] == [
        {"id": artifact.id, "filename": "result.txt", "size_bytes": 17}
    ]

    other_headers = await make_headers()
    hidden = await client.get(
        f"{RUNS_URL}/{execution.run_id}/executions/{execution.id}",
        headers=other_headers,
    )
    assert hidden.status_code == 404

    missing = await client.get(
        f"{RUNS_URL}/{execution.run_id}/executions/{uuid.uuid4()}", headers=headers
    )
    assert missing.status_code == 404
