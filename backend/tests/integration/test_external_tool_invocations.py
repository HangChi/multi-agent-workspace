"""Invocation persistence and safe Run projection contract tests."""

import asyncio

import pytest
from sqlalchemy import event, select
from sqlalchemy.engine import Engine

from app.models.external_invocation import ExternalToolInvocation
from app.models.external_tool import ExternalToolServer, ExternalToolVersion
from app.models.run import Run
from app.services import quota_service
from app.services.external_invocation_service import (
    list_run_invocations,
    record_invocation,
)


async def _run_fixture(client, db_session, headers, owner):
    request_response = await client.post(
        "/api/v1/requests",
        headers=headers,
        json={"title": "external invocation", "description": "safe projection", "priority": "normal"},
    )
    assert request_response.status_code == 201
    server = ExternalToolServer(
        owner_id=owner.id,
        created_by=owner.id,
        name="Projection Server",
        description="fixture",
        endpoint_url="https://8.8.8.8/invocations",
        normalized_endpoint_url="https://8.8.8.8/invocations",
        maintainer="Platform",
        auth_mode="none",
        status="published",
        current_validation_status="passed",
    )
    db_session.add(server)
    await db_session.flush()
    version = ExternalToolVersion(
        server_id=server.id,
        external_name="lookup",
        stable_name="mcp_projection_lookup_v1",
        version="1.0.0+invocation",
        description="Lookup",
        input_schema={"type": "object"},
        required_permissions=["records.read"],
        definition_hash="invocation-digest",
        availability="valid",
    )
    db_session.add(version)
    await db_session.flush()
    run = Run(
        request_id=request_response.json()["id"],
        owner_id=owner.id,
        assignee_type="agent",
        assignee_id="00000000-0000-0000-0000-000000000001",
        strategy="direct",
        status="failed",
    )
    db_session.add(run)
    await db_session.flush()
    return run, server, version


@pytest.mark.asyncio
async def test_run_projection_contains_bounded_external_invocation_metadata_without_raw_values(
    client, db_session, make_headers, make_user
):
    owner, _ = await make_user(role="member")
    headers = await make_headers(user=owner)
    run, server, version = await _run_fixture(client, db_session, headers, owner)
    db_session.add(
        ExternalToolInvocation(
            run_id=run.id,
            owner_id=owner.id,
            server_id=server.id,
            tool_version_id=version.id,
            call_id="call-1",
            status="auth_error",
            duration_ms=42,
            input_summary={"keys": ["query"]},
            output_summary=None,
            policy_result={"allowed": True},
            error_summary="外部工具服务器认证失败",
        )
    )
    await db_session.commit()

    response = await client.get(f"/api/v1/runs/{run.id}", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["external_tool_invocations"]) == 1
    projection = body["external_tool_invocations"][0]
    assert projection["server_name"] == "Projection Server"
    assert projection["stable_name"] == version.stable_name
    assert projection["version"] == version.version
    assert projection["status"] == "auth_error"
    assert "ciphertext" not in response.text
    assert "query-value" not in response.text


@pytest.mark.asyncio
async def test_run_polling_selects_only_external_invocation_summary_columns(
    client, db_session, make_headers, make_user
):
    owner, _ = await make_user(role="member")
    headers = await make_headers(user=owner)
    run, server, version = await _run_fixture(client, db_session, headers, owner)
    version.input_schema = {"type": "object", "description": "large-schema" * 4096}
    db_session.add(ExternalToolInvocation(
        run_id=run.id, owner_id=owner.id, server_id=server.id, tool_version_id=version.id,
        call_id="summary-only", status="succeeded", duration_ms=12,
        input_summary={"keys": ["query"]}, output_summary={"text": "large-result" * 4096},
        policy_result={"allowed": True},
    ))
    await db_session.commit()
    statements = []

    def capture(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement)

    event.listen(Engine, "before_cursor_execute", capture)
    try:
        response = await client.get(f"/api/v1/runs/{run.id}", headers=headers)
    finally:
        event.remove(Engine, "before_cursor_execute", capture)
    assert response.status_code == 200, response.text
    projection, = response.json()["external_tool_invocations"]
    assert projection["server_name"] == server.name
    assert projection["stable_name"] == version.stable_name
    assert projection["version"] == version.version
    assert projection["duration_ms"] == 12
    assert projection["error_category"] is None
    queries = [sql.split("FROM", 1)[0] for sql in statements
               if sql.lstrip().startswith("SELECT") and "FROM external_tool_invocations" in sql]
    assert len(queries) == 1
    for excluded in (".input_summary", ".output_summary", ".policy_result", ".error_summary",
                     ".input_schema", ".required_permissions", ".endpoint_url", ".description"):
        assert excluded not in queries[0]


@pytest.mark.asyncio
async def test_invocation_records_all_safe_error_categories_without_raw_values(
    client, db_session, make_headers, make_user
):
    owner, _ = await make_user(role="member")
    headers = await make_headers(user=owner)
    run, server, version = await _run_fixture(client, db_session, headers, owner)
    categories = [
        "succeeded",
        "parameter_error",
        "policy_denied",
        "transport_error",
        "auth_error",
        "timeout",
        "tool_error",
        "result_truncated",
        "cancelled",
    ]
    for index, category in enumerate(categories):
        await record_invocation(
            db_session,
            run_id=run.id,
            owner_id=owner.id,
            server_id=server.id,
            tool_version_id=version.id,
            grant_id=None,
            credential_id=None,
            call_id=f"call-{index}",
            status=category,
            duration_ms=index,
            arguments={"secret": "do-not-persist"},
            result={"secret": "do-not-persist", "result_truncated": category == "result_truncated"},
            policy_result={"allowed": category not in {"policy_denied", "cancelled"}},
            error_category=None if category == "succeeded" else category,
        )
    await db_session.commit()

    rows = await list_run_invocations(db_session, run_id=run.id)
    assert {row["status"] for row in rows} == set(categories)
    assert all("do-not-persist" not in str(row) for row in rows)
    persisted = (await db_session.execute(select(ExternalToolInvocation))).scalars().all()
    assert all(row.duration_ms >= 0 for row in persisted)
    assert all("secret" not in str(row.policy_result) for row in persisted)


@pytest.mark.asyncio
async def test_external_invocation_concurrency_limit_never_exceeds_one_slot():
    from app.executors.external_tool import (
        ExternalToolExecutionError,
        ExternalToolExecutor,
    )

    active = 0
    max_active = 0
    leases = 0

    async def acquire(_endpoint, *, wait_seconds, abort_check):
        nonlocal active, max_active, leases
        if active:
            return None
        active += 1
        max_active = max(max_active, active)
        leases += 1
        return f"lease-{leases}"

    async def release(_endpoint, _lease):
        nonlocal active
        active -= 1

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def initialize(self):
            return None

        async def call_tool(self, _name, _arguments, *, timeout=None):
            await asyncio.sleep(0.02)
            return {"content": [{"type": "text", "text": "ok"}]}

    def executor():
        return ExternalToolExecutor(
            stable_name="mcp_concurrency_test",
            external_name="lookup",
            description="fixture",
            endpoint="https://fixture.example.test/rpc",
            input_schema={"type": "object"},
            permission_snapshot=[],
            required_permissions=[],
            client_factory=lambda *_args, **_kwargs: Client(),
            acquire_concurrency_slot=acquire,
            release_concurrency_slot=release,
        )

    async def call_one():
        try:
            await executor().execute({})
            return "succeeded"
        except ExternalToolExecutionError as exc:
            return exc.category

    results = await asyncio.gather(call_one(), call_one())
    assert max_active == 1
    assert sorted(results) == ["succeeded", "timeout"]
    assert active == 0


@pytest.mark.asyncio
async def test_external_invocation_uses_existing_user_quota_gate(
    client, db_session, make_headers, make_user, monkeypatch
):
    owner, _ = await make_user(role="member")
    headers = await make_headers(user=owner)
    run, _server, _version = await _run_fixture(client, db_session, headers, owner)
    run.cost_cents = 100
    run.status = "completed"
    await db_session.commit()
    monkeypatch.setattr(quota_service, "MONTHLY_QUOTA_CENTS", 100)

    with pytest.raises(quota_service.QuotaExceeded):
        await quota_service.assert_user_quota(db_session, owner.id)
