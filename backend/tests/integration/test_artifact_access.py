"""002 制品下载 API：归属信息隐藏、重定向与服务端指纹。"""

from __future__ import annotations

import hashlib
import uuid

from app.models.artifact import Artifact

RUNS_URL = "/api/v1/runs"


async def test_owner_downloads_artifact_with_redirect_and_sha256(
    client, make_headers, make_user, sandbox_factory, db_session, monkeypatch
):
    owner, _ = await make_user()
    headers = await make_headers(user=owner)
    execution = await sandbox_factory(owner=owner, status="ok", exit_code=0)
    content = b"downloadable bytes"
    artifact = Artifact(
        run_id=execution.run_id,
        step_id=execution.step_id,
        object_key=f"artifacts/{execution.run_id}/{execution.id}/result.txt",
        filename="result.txt",
        size_bytes=len(content),
        mime_type="text/plain",
        sha256=hashlib.sha256(content).hexdigest(),
    )
    db_session.add(artifact)
    await db_session.commit()
    monkeypatch.setattr(
        "app.api.v1.runs.artifact_service.presign_get",
        lambda key: f"https://objects.example.test/download/{key}",
    )

    response = await client.get(
        f"{RUNS_URL}/{execution.run_id}/artifacts/{artifact.id}",
        headers=headers,
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert response.headers["location"].endswith(artifact.object_key)

    # 浏览器链接无法带 Bearer 头：前端用同一端点取回 URL 再跳转
    resolved = await client.get(
        f"{RUNS_URL}/{execution.run_id}/artifacts/{artifact.id}",
        headers=headers,
        params={"redirect": "false"},
    )
    assert resolved.status_code == 200
    assert resolved.json()["url"].endswith(artifact.object_key)
    assert resolved.json()["filename"] == "result.txt"

    other_headers = await make_headers()
    hidden = await client.get(
        f"{RUNS_URL}/{execution.run_id}/artifacts/{artifact.id}",
        headers=other_headers,
        follow_redirects=False,
    )
    assert hidden.status_code == 404


async def test_artifact_download_hides_cross_run_and_unknown_ids(
    client, make_headers, make_user, sandbox_factory, db_session
):
    owner, _ = await make_user()
    headers = await make_headers(user=owner)
    execution = await sandbox_factory(owner=owner, status="ok", exit_code=0)
    artifact = Artifact(
        run_id=execution.run_id,
        step_id=execution.step_id,
        object_key="artifacts/result.bin",
        filename="result.bin",
        size_bytes=1,
        mime_type="application/octet-stream",
        sha256="b" * 64,
    )
    db_session.add(artifact)
    await db_session.commit()

    unknown = await client.get(
        f"{RUNS_URL}/{execution.run_id}/artifacts/{uuid.uuid4()}",
        headers=headers,
        follow_redirects=False,
    )
    assert unknown.status_code == 404

    other_execution = await sandbox_factory(owner=owner, status="ok", exit_code=0)
    cross_run = await client.get(
        f"{RUNS_URL}/{other_execution.run_id}/artifacts/{artifact.id}",
        headers=headers,
        follow_redirects=False,
    )
    assert cross_run.status_code == 404
