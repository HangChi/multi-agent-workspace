"""Marketplace browse and reviewer lifecycle contract tests."""

import pytest

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
    admin_response = await client.get("/api/v1/marketplace/tools", headers=admin_headers)
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
