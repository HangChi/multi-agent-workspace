"""External lifecycle tests: credential rotation, version upgrade, and pause/revoke."""

import pytest
from sqlalchemy import select

from app.models.agent import Agent
from app.models.external_tool import (
    ExternalToolGrant,
    ExternalToolServer,
    ExternalToolVersion,
    MarketplaceListing,
    RunExternalToolSnapshot,
    ToolInstallation,
)
from app.models.request import Request
from app.models.run import Run
from app.services.external_credential_service import create_credential
from app.services.external_grant_service import create_grant


async def _server_version_listing(db, owner_id: str, *, version: str, status: str = "published"):
    server = await db.scalar(select(ExternalToolServer).where(ExternalToolServer.owner_id == owner_id))
    if server is None:
        server = ExternalToolServer(
            owner_id=owner_id,
            created_by=owner_id,
            name="Lifecycle Server",
            description="Lifecycle fixture",
            endpoint_url="https://8.8.8.8/lifecycle",
            normalized_endpoint_url="https://8.8.8.8/lifecycle",
            maintainer="Platform",
            auth_mode="static_bearer",
            status="published",
            current_validation_status="passed",
        )
        db.add(server)
        await db.flush()
    tool = ExternalToolVersion(
        server_id=server.id,
        external_name="lookup",
        stable_name=f"mcp_lifecycle_lookup_{version.replace('.', '_')}",
        version=version,
        description="Lifecycle lookup",
        input_schema={"type": "object"},
        required_permissions=["records.read"],
        definition_hash=f"digest-{version}",
        availability="valid",
    )
    db.add(tool)
    await db.flush()
    listing = MarketplaceListing(
        tool_version_id=tool.id,
        status=status,
        usage_summary="Lifecycle lookup",
        data_handling="Sends only lookup arguments",
        restrictions={"timeout_seconds": 30},
    )
    db.add(listing)
    await db.flush()
    return server, tool, listing


@pytest.mark.asyncio
async def test_credential_rotation_revocation_is_immediate_and_secret_safe(
    client, db_session, make_user, make_headers
):
    owner, _ = await make_user(role="admin")
    headers = await make_headers(user=owner)
    server, _tool, _listing = await _server_version_listing(db_session, owner.id, version="1.0.0")
    first = await create_credential(
        db_session,
        server_id=server.id,
        owner_id=owner.id,
        created_by=owner.id,
        label="old",
        kind="bearer",
        value="old-secret-canary",
    )
    await db_session.commit()

    rotated = await client.post(
        f"/api/v1/external-tool-servers/{server.id}/credentials/rotate",
        headers=headers,
        json={"label": "new", "kind": "bearer", "value": "new-secret-canary"},
    )
    assert rotated.status_code == 200, rotated.text
    assert rotated.json()["status"] == "active"
    assert "old-secret-canary" not in rotated.text
    assert "new-secret-canary" not in rotated.text

    await db_session.refresh(first)
    assert first.status == "superseded"
    second_id = rotated.json()["id"]
    revoked = await client.post(
        f"/api/v1/external-tool-servers/{server.id}/credentials/{second_id}/revoke",
        headers=headers,
    )
    assert revoked.status_code == 200
    assert revoked.json()["status"] == "revoked"
    assert "ciphertext" not in revoked.text


@pytest.mark.asyncio
async def test_pause_resume_revoke_listing_and_explicit_upgrade_preserve_history(
    client, db_session, make_user, make_headers
):
    owner, _ = await make_user(role="admin")
    headers = await make_headers(user=owner)
    server, old_tool, old_listing = await _server_version_listing(
        db_session, owner.id, version="1.0.0"
    )
    _server, new_tool, new_listing = await _server_version_listing(
        db_session, owner.id, version="1.1.0"
    )
    agent = Agent(
        owner_id=owner.id,
        name="Lifecycle Agent",
        system_prompt="Use tools safely",
        model="test-model",
        tools=[],
    )
    db_session.add(agent)
    await db_session.flush()
    installation = ToolInstallation(
        listing_id=old_listing.id,
        owner_id=owner.id,
        installed_by=owner.id,
        status="installed",
        version_policy="allow_patch",
    )
    db_session.add(installation)
    await db_session.flush()
    credential = await create_credential(
        db_session,
        server_id=server.id,
        owner_id=owner.id,
        created_by=owner.id,
        label="active",
        kind="bearer",
        value="lifecycle-secret",
    )
    grant = await create_grant(
        db_session,
        agent_id=agent.id,
        user_id=owner.id,
        installation_id=installation.id,
        tool_version_id=old_tool.id,
        credential_id=credential.id,
        permissions=["records.read"],
    )
    request = Request(owner_id=owner.id, title="lifecycle", status="completed")
    db_session.add(request)
    await db_session.flush()
    run = Run(
        request_id=request.id,
        owner_id=owner.id,
        assignee_type="agent",
        assignee_id=agent.id,
        strategy="direct",
        status="completed",
    )
    db_session.add(run)
    await db_session.flush()
    old_snapshot = RunExternalToolSnapshot(
        run_id=run.id,
        grant_id=grant.id,
        server_id=server.id,
        tool_version_id=old_tool.id,
        stable_name=old_tool.stable_name,
        external_name=old_tool.external_name,
        version=old_tool.version,
        permission_snapshot=["records.read"],
        schema_snapshot={"input": old_tool.input_schema},
        credential_id=credential.id,
    )
    db_session.add(old_snapshot)
    await db_session.commit()

    paused = await client.post(
        f"/api/v1/marketplace/tools/{old_listing.id}/pause",
        headers=headers,
        json={"reason": "maintenance window"},
    )
    assert paused.status_code == 200
    assert paused.json()["status"] == "paused"
    resumed = await client.post(
        f"/api/v1/marketplace/tools/{old_listing.id}/resume",
        headers=headers,
        json={"reason": "maintenance complete"},
    )
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "published"

    upgraded = await client.post(
        f"/api/v1/marketplace/installations/{installation.id}/upgrade",
        headers=headers,
        json={"listing_id": new_listing.id, "reason": "approved patch upgrade"},
    )
    assert upgraded.status_code == 200, upgraded.text
    assert upgraded.json()["listing_id"] == new_listing.id
    refreshed_grant = await db_session.get(ExternalToolGrant, grant.id)
    await db_session.refresh(refreshed_grant)
    assert refreshed_grant.tool_version_id == new_tool.id
    await db_session.refresh(old_snapshot)
    assert old_snapshot.version == "1.0.0"

    revoked = await client.post(
        f"/api/v1/marketplace/tools/{new_listing.id}/revoke",
        headers=headers,
        json={"reason": "integration retired"},
    )
    assert revoked.status_code == 200
    assert revoked.json()["status"] == "revoked"
    assert revoked.json()["version"] == "1.1.0"
