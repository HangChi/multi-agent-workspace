"""Marketplace installation and Agent external-grant contract tests."""

import pytest

from app.models.agent import Agent
from app.models.external_tool import (
    ExternalToolServer,
    ExternalToolVersion,
    MarketplaceListing,
)


async def _published_listing(db_session, owner_id: str):
    server = ExternalToolServer(
        owner_id=owner_id,
        created_by=owner_id,
        name="Grant Server",
        description="Grant fixture",
        endpoint_url="https://8.8.8.8/grants",
        normalized_endpoint_url="https://8.8.8.8/grants",
        maintainer="Platform",
        auth_mode="none",
        status="pending_review",
        current_validation_status="passed",
    )
    db_session.add(server)
    await db_session.flush()
    version = ExternalToolVersion(
        server_id=server.id,
        external_name="lookup",
        stable_name="mcp_grant_server_lookup_v1",
        version="1.0.0+grant",
        description="Lookup approved records",
        input_schema={"type": "object", "properties": {"q": {"type": "string"}}},
        required_permissions=["records.read"],
        definition_hash="grant-digest",
        availability="valid",
    )
    db_session.add(version)
    await db_session.flush()
    listing = MarketplaceListing(
        tool_version_id=version.id,
        status="published",
        usage_summary="Lookup records",
        data_handling="Sends query to approved server",
        restrictions={},
    )
    db_session.add(listing)
    await db_session.flush()
    return server, version, listing


async def _agent(db_session, owner_id: str) -> Agent:
    agent = Agent(
        owner_id=owner_id,
        name="Grant Agent",
        system_prompt="Use authorized tools only",
        model="test-model",
        tools=[],
    )
    db_session.add(agent)
    await db_session.flush()
    return agent


@pytest.mark.asyncio
async def test_install_and_grant_pin_version_and_permissions(client, db_session, make_user, make_headers):
    owner, _ = await make_user(role="member")
    headers = await make_headers(user=owner)
    _server, version, listing = await _published_listing(db_session, owner.id)
    agent = await _agent(db_session, owner.id)
    await db_session.commit()

    installed = await client.post(
        f"/api/v1/marketplace/tools/{listing.id}/install",
        headers=headers,
        json={"version_policy": "pinned"},
    )
    assert installed.status_code == 201
    assert installed.json()["listing_id"] == listing.id

    granted = await client.post(
        f"/api/v1/agents/{agent.id}/external-tool-grants",
        headers=headers,
        json={
            "installation_id": installed.json()["id"],
            "tool_version_id": version.id,
            "permissions": ["records.read"],
        },
    )
    assert granted.status_code == 201
    body = granted.json()
    assert body["version"] == version.version
    assert body["permissions"] == ["records.read"]
    assert body["stable_name"] == version.stable_name
    assert "credential" not in body or body["credential"] is None
    assert "ciphertext" not in granted.text

    fetched = await client.get(f"/api/v1/agents/{agent.id}/external-tool-grants", headers=headers)
    assert fetched.status_code == 200
    assert fetched.json()["total"] == 1
    assert fetched.json()["items"][0]["tool_version_id"] == version.id

    revoked = await client.delete(
        f"/api/v1/agents/{agent.id}/external-tool-grants/{body['id']}", headers=headers
    )
    assert revoked.status_code == 204


@pytest.mark.asyncio
async def test_grant_rejects_wrong_owner_permission_escalation_and_viewer(client, db_session, make_user, make_headers):
    owner, _ = await make_user(role="member")
    stranger, _ = await make_user(role="member")
    owner_headers = await make_headers(user=owner)
    stranger_headers = await make_headers(user=stranger)
    _server, version, listing = await _published_listing(db_session, owner.id)
    agent = await _agent(db_session, owner.id)
    await db_session.commit()

    install = await client.post(
        f"/api/v1/marketplace/tools/{listing.id}/install",
        headers=owner_headers,
        json={"version_policy": "pinned"},
    )
    assert install.status_code == 201
    install_id = install.json()["id"]

    escalation = await client.post(
        f"/api/v1/agents/{agent.id}/external-tool-grants",
        headers=owner_headers,
        json={"installation_id": install_id, "tool_version_id": version.id, "permissions": ["records.write"]},
    )
    assert escalation.status_code == 400

    wrong_owner = await client.post(
        f"/api/v1/agents/{agent.id}/external-tool-grants",
        headers=stranger_headers,
        json={"installation_id": install_id, "tool_version_id": version.id, "permissions": ["records.read"]},
    )
    assert wrong_owner.status_code == 403

    viewer_headers = await make_headers(role="viewer")
    denied = await client.post(
        f"/api/v1/marketplace/tools/{listing.id}/install",
        headers=viewer_headers,
        json={"version_policy": "pinned"},
    )
    assert denied.status_code == 403
