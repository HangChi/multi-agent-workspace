"""Integration coverage for external MCP validation outcomes."""

import pytest
from sqlalchemy import select

from app.executors.mcp_client import MCPProtocolError, MCPTool
from app.models.external_tool import ExternalToolServer, ExternalToolVersion


class FakeClient:
    def __init__(self, *, tools=None, initialize_error=None, list_error=None):
        self.tools = tools or []
        self.initialize_error = initialize_error
        self.list_error = list_error

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def initialize(self):
        if self.initialize_error:
            raise self.initialize_error
        return {"protocolVersion": "2025-06-18"}

    async def list_tools(self):
        if self.list_error:
            raise self.list_error
        return self.tools


async def _server(db_session, owner_id: str) -> ExternalToolServer:
    server = ExternalToolServer(
        owner_id=owner_id,
        created_by=owner_id,
        name="Validation Fixture",
        description="Fixture",
        endpoint_url="https://8.8.8.8/mcp",
        normalized_endpoint_url="https://8.8.8.8/mcp",
        maintainer="Test",
        auth_mode="none",
        status="draft",
    )
    db_session.add(server)
    await db_session.flush()
    return server


def _patch_endpoint(monkeypatch):
    from app.executors import mcp_security
    from app.executors.mcp_security import ValidatedEndpoint

    monkeypatch.setattr(
        mcp_security,
        "validate_endpoint",
        lambda url, **kwargs: ValidatedEndpoint(url, "8.8.8.8", 443, ("8.8.8.8",)),
    )


@pytest.mark.asyncio
async def test_unreachable_server_persists_safe_failed_validation(db_session, make_user, monkeypatch):
    from app.services import external_validation_service

    user, _ = await make_user(role="admin")
    server = await _server(db_session, user.id)
    _patch_endpoint(monkeypatch)

    result = await external_validation_service.validate_server(
        db_session,
        server=server,
        requested_by=user.id,
        client_factory=lambda _endpoint, **_: FakeClient(initialize_error=TimeoutError()),
    )
    assert result.status == "failed"
    assert result.connection_status == "failed"
    assert result.safe_error
    assert "8.8.8.8" not in result.safe_error
    assert server.status == "validation_failed"


@pytest.mark.asyncio
async def test_auth_failure_and_partial_discovery_are_classified_and_audited(
    db_session, make_user, monkeypatch
):
    from app.models.audit_log import AuditLog
    from app.services import external_validation_service

    user, _ = await make_user(role="admin")
    _patch_endpoint(monkeypatch)

    auth_server = await _server(db_session, user.id)
    auth_result = await external_validation_service.validate_server(
        db_session,
        server=auth_server,
        requested_by=user.id,
        client_factory=lambda _endpoint, **_: FakeClient(
            initialize_error=MCPProtocolError("authentication failed")
        ),
    )
    assert auth_result.status == "failed"
    assert auth_result.auth_status == "failed"

    partial_server = await _server(db_session, user.id)
    partial_server.endpoint_url = "https://8.8.8.8/other"
    partial_server.normalized_endpoint_url = "https://8.8.8.8/other"
    partial_result = await external_validation_service.validate_server(
        db_session,
        server=partial_server,
        requested_by=user.id,
        client_factory=lambda _endpoint, **_: FakeClient(
            tools=[MCPTool("valid", "valid", {"type": "object"}), object()]
        ),
    )
    assert partial_result.status == "partial"
    assert partial_result.discovered_count == 2
    assert partial_result.valid_count == 1

    await db_session.commit()
    audits = (await db_session.execute(
        select(AuditLog).where(AuditLog.action == "external_server_validate")
    )).scalars().all()
    assert len(audits) >= 2
    assert all("authentication failed" not in str(audit.detail) for audit in audits)


@pytest.mark.asyncio
async def test_complete_discovery_creates_versions_and_digest_tracks_drift(
    db_session, make_user, monkeypatch
):
    from app.services import external_validation_service

    user, _ = await make_user(role="admin")
    _patch_endpoint(monkeypatch)
    server = await _server(db_session, user.id)
    first_tool = MCPTool("search", "Search v1", {"type": "object", "properties": {}})
    first = await external_validation_service.validate_server(
        db_session,
        server=server,
        requested_by=user.id,
        client_factory=lambda _endpoint, **_: FakeClient(tools=[first_tool]),
    )
    second_tool = MCPTool(
        "search", "Search v2", {"type": "object", "properties": {"q": {"type": "string"}}}
    )
    second = await external_validation_service.validate_server(
        db_session,
        server=server,
        requested_by=user.id,
        client_factory=lambda _endpoint, **_: FakeClient(tools=[second_tool]),
    )

    versions = (await db_session.execute(
        select(ExternalToolVersion).where(ExternalToolVersion.server_id == server.id)
    )).scalars().all()
    assert first.status == "passed"
    assert second.status == "partial"
    assert second.safe_error == "MCP tool definitions changed; reviewer approval is required"
    assert first.definition_digest != second.definition_digest
    assert len(versions) == 2
    assert {version.availability for version in versions} == {"stale", "valid"}
