"""Marketplace browse and reviewer lifecycle contract tests."""

import pytest
from sqlalchemy import select

from app.models.external_tool import (
    ExternalToolServer,
    ExternalToolVersion,
    MarketplaceListing,
)


async def _version(db_session, user_id: str, *, name: str = "search") -> ExternalToolVersion:
    server = ExternalToolServer(
        owner_id=user_id,
        created_by=user_id,
        name=f"Server {name}",
        description="Approved fixture",
        endpoint_url=f"https://8.8.8.8/{name}",
        normalized_endpoint_url=f"https://8.8.8.8/{name}",
        maintainer="Platform",
        auth_mode="none",
        status="pending_review",
        current_validation_status="passed",
    )
    db_session.add(server)
    await db_session.flush()
    version = ExternalToolVersion(
        server_id=server.id,
        external_name=name,
        stable_name=f"mcp_server_{name}_v1",
        version="1.0.0+digest",
        description=f"Find {name} data",
        input_schema={"type": "object"},
        output_schema=None,
        required_permissions=["knowledge.read"],
        definition_hash=f"{name}-digest",
        availability="valid",
    )
    db_session.add(version)
    await db_session.flush()
    return version


@pytest.mark.asyncio
async def test_marketplace_browse_exposes_published_validated_listings_only(
    client, db_session, make_headers, make_user
):
    admin_headers = await make_headers(role="admin")
    member_headers = await make_headers(role="member")
    owner, _ = await make_user(role="admin")
    published = await _version(db_session, owner.id, name="published")
    hidden = await _version(db_session, owner.id, name="hidden")
    db_session.add_all([
        MarketplaceListing(
            tool_version_id=published.id,
            status="published",
            usage_summary="Search approved content",
            data_handling="Queries are sent to the approved server",
            restrictions={"timeout_seconds": 10},
        ),
        MarketplaceListing(
            tool_version_id=hidden.id,
            status="pending_review",
            usage_summary="Pending",
            data_handling="Pending",
            restrictions={},
        ),
    ])
    await db_session.commit()

    response = await client.get("/api/v1/marketplace/tools?q=published", headers=member_headers)
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["stable_name"] == published.stable_name
    assert "endpoint_url" not in response.text
    admin_response = await client.get("/api/v1/marketplace/tools?q=published", headers=admin_headers)
    assert admin_response.status_code == 200
    assert admin_response.json()["total"] == 1


@pytest.mark.asyncio
async def test_reviewer_approval_requires_passed_validation_and_creates_listing(
    client, db_session, make_headers, make_user
):
    owner, _ = await make_user(role="admin")
    reviewer_headers = await make_headers(role="admin")
    version = await _version(db_session, owner.id)
    await db_session.commit()

    response = await client.post(
        f"/api/v1/external-tool-servers/{version.server_id}/tools/{version.id}/approve",
        headers=reviewer_headers,
        json={"reason": "Source and data handling reviewed"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "published"
    assert response.json()["stable_name"] == version.stable_name


@pytest.mark.asyncio
async def test_published_listing_can_be_paused_then_revoked_with_reasons(
    client, db_session, make_headers, make_user
):
    owner, _ = await make_user(role="admin")
    admin_headers = await make_headers(role="admin")
    version = await _version(db_session, owner.id, name="lifecycle")
    listing = MarketplaceListing(
        tool_version_id=version.id,
        status="published",
        usage_summary="Search approved content",
        data_handling="Requests go to approved server",
        restrictions={},
    )
    db_session.add(listing)
    await db_session.commit()

    paused = await client.post(
        f"/api/v1/marketplace/tools/{listing.id}/pause",
        headers=admin_headers,
        json={"reason": "Temporarily paused for review"},
    )
    assert paused.status_code == 200
    assert paused.json()["status"] == "paused"

    revoked = await client.post(
        f"/api/v1/marketplace/tools/{listing.id}/revoke",
        headers=admin_headers,
        json={"reason": "Retired after security review"},
    )
    assert revoked.status_code == 200
    assert revoked.json()["status"] == "revoked"


@pytest.mark.asyncio
async def test_revoking_the_last_published_listing_unpublishes_its_server(
    client, db_session, make_headers, make_user
):
    """撤销最后一个已发布 listing 后，服务器退回 pending_review。

    会话是 autoflush=False：如果 _refresh_server_status 里的计数看不到本次未提交的
    listing.status 变更，服务器会停在 published，治理状态机与契约不一致。
    """
    owner, _ = await make_user(role="admin")
    admin_headers = await make_headers(role="admin")
    version = await _version(db_session, owner.id, name="last-listing")
    server_row = await db_session.get(ExternalToolServer, version.server_id)
    assert server_row is not None
    server_id = server_row.id
    server_row.status = "published"
    listing = MarketplaceListing(
        tool_version_id=version.id,
        status="published",
        usage_summary="Search approved content",
        data_handling="Requests go to approved server",
        restrictions={},
    )
    db_session.add(listing)
    await db_session.commit()

    revoked = await client.post(
        f"/api/v1/marketplace/tools/{listing.id}/revoke",
        headers=admin_headers,
        json={"reason": "Retired after security review"},
    )
    assert revoked.status_code == 200

    db_session.expire_all()
    server = await db_session.get(ExternalToolServer, server_id)
    assert server is not None and server.status == "pending_review"


@pytest.mark.asyncio
async def test_approving_a_tool_publishes_its_server_and_grants_can_snapshot(
    client, db_session, make_headers, make_user
):
    """Approval must advance the server, not only the listing.

    Grant snapshotting and listing resume both require a published server, so a
    server that stays ``pending_review`` after its tool is approved makes every
    external tool unusable and every pause permanent. This walks the real
    governance path -- register, validate, approve -- instead of inserting a
    published server, because the gap only exists on that path.
    """
    from app.tasks.validate_external_server import validate_external_server

    original_delay = validate_external_server.delay
    validate_external_server.delay = lambda server_id, requested_by: None
    try:
        admin, _ = await make_user(role="admin")
        admin_headers = await make_headers(user=admin)
        created = await client.post(
            "/api/v1/external-tool-servers",
            headers=admin_headers,
            json={
                "name": "Governance Path Server",
                "description": "Registered through the real API",
                "endpoint_url": "https://8.8.8.8/governance",
                "maintainer": "Platform",
                "auth_mode": "none",
            },
        )
        assert created.status_code == 201
        assert created.json()["status"] == "pending_review"
        server_id = created.json()["id"]
    finally:
        validate_external_server.delay = original_delay

    from app.executors.mcp_client import MCPTool
    from app.services import external_validation_service

    class _FixtureClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def initialize(self):
            return {"protocolVersion": "2025-06-18"}

        async def list_tools(self):
            return [MCPTool(name="search", description="Find records",
                            input_schema={"type": "object"})]

    server = await db_session.get(ExternalToolServer, server_id)
    await external_validation_service.validate_server(
        db_session, server=server, requested_by=admin.id,
        client_factory=lambda *args, **kwargs: _FixtureClient(),
    )
    await db_session.refresh(server)
    assert server.status == "pending_review"
    tool_version = (await db_session.execute(
        select(ExternalToolVersion).where(ExternalToolVersion.server_id == server_id)
    )).scalars().first()
    assert tool_version is not None

    approved = await client.post(
        f"/api/v1/external-tool-servers/{server_id}/tools/{tool_version.id}/approve",
        headers=admin_headers,
        json={"reason": "Reviewed and approved for deployment"},
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "published"
    await db_session.refresh(server)
    assert server.status == "published"
