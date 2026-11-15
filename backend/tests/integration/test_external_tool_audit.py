"""External invocation audit query and canary redaction tests."""

import pytest
from sqlalchemy import select

from app.models.audit_log import AuditLog
from app.models.external_invocation import ExternalToolInvocation
from app.models.external_tool import ExternalToolServer, ExternalToolVersion
from app.models.request import Request
from app.models.run import Run
from app.services.external_audit_service import write_external_audit


async def _audit_fixture(db, owner_id: str):
    request = Request(owner_id=owner_id, title="audit request", status="completed")
    db.add(request)
    await db.flush()
    run = Run(
        request_id=request.id,
        owner_id=owner_id,
        assignee_type="agent",
        assignee_id="00000000-0000-0000-0000-000000000001",
        strategy="direct",
        status="completed",
    )
    db.add(run)
    server = ExternalToolServer(
        owner_id=owner_id,
        created_by=owner_id,
        name="Audit Server",
        description="Audit fixture",
        endpoint_url="https://8.8.8.8/audit",
        normalized_endpoint_url="https://8.8.8.8/audit",
        maintainer="Platform",
        auth_mode="none",
        status="published",
        current_validation_status="passed",
    )
    db.add(server)
    await db.flush()
    version = ExternalToolVersion(
        server_id=server.id,
        external_name="audit_lookup",
        stable_name="mcp_audit_lookup_v1",
        version="1.0.0",
        description="Audit lookup",
        input_schema={"type": "object"},
        required_permissions=[],
        definition_hash="audit-digest",
        availability="valid",
    )
    db.add(version)
    await db.flush()
    return run, server, version


@pytest.mark.asyncio
async def test_external_invocation_audit_query_filters_and_redacts_canaries(
    client, db_session, make_user, make_headers
):
    owner, _ = await make_user(role="admin")
    headers = await make_headers(user=owner)
    run, server, version = await _audit_fixture(db_session, owner.id)
    db_session.add(
        ExternalToolInvocation(
            run_id=run.id,
            owner_id=owner.id,
            server_id=server.id,
            tool_version_id=version.id,
            call_id="audit-call",
            status="auth_error",
            duration_ms=17,
            input_summary={"keys": ["query"]},
            output_summary={"keys": [], "result_truncated": False},
            policy_result={"allowed": False, "reason": "credential revoked"},
            error_summary="外部工具服务器认证失败",
        )
    )
    await write_external_audit(
        db_session,
        user_id=owner.id,
        action="external_tool_invocation",
        target_type="run",
        target_id=run.id,
        detail={
            "server_id": server.id,
            "tool_version_id": version.id,
            "status": "auth_error",
            "credential": "credential-canary-must-not-persist",
        },
    )
    await db_session.commit()

    response = await client.get(
        "/api/v1/audit-logs/external-invocations",
        headers=headers,
        params={"server_id": server.id, "status": "auth_error"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["run_id"] == run.id
    assert body["items"][0]["status"] == "auth_error"
    assert "credential-canary" not in response.text
    assert "ciphertext" not in response.text

    audit_rows = (await db_session.execute(select(AuditLog))).scalars().all()
    assert any(row.action == "external_tool_invocation" for row in audit_rows)
    assert all("credential-canary" not in str(row.detail) for row in audit_rows)
    assert all("raw" not in str(row.detail).lower() for row in audit_rows)


@pytest.mark.asyncio
async def test_external_audit_helper_rejects_unknown_actions_without_writing(
    db_session, make_user
):
    owner, _ = await make_user(role="admin")
    with pytest.raises(ValueError):
        await write_external_audit(
            db_session,
            user_id=owner.id,
            action="external_unknown_action",
            target_type="run",
            target_id="run-id",
            detail={"secret": "do-not-write"},
        )
