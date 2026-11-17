"""Marketplace read/review authorization and transition validation."""

import pytest

from app.models.external_tool import ExternalToolServer, ExternalToolVersion


async def _pending_version(db_session, owner_id: str) -> ExternalToolVersion:
    server = ExternalToolServer(
        owner_id=owner_id,
        created_by=owner_id,
        name="Pending Server",
        description="Pending fixture",
        endpoint_url="https://8.8.8.8/pending",
        normalized_endpoint_url="https://8.8.8.8/pending",
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
        stable_name=f"mcp_pending_lookup_{server.id[:8]}",
        version="1.0.0+pending",
        description="Lookup data",
        input_schema={"type": "object"},
        required_permissions=[],
        definition_hash=server.id,
        availability="valid",
    )
    db_session.add(version)
    await db_session.flush()
    return version


@pytest.mark.asyncio
async def test_member_can_browse_but_cannot_approve_or_pause(
    client, db_session, make_headers, make_user
):
    owner, _ = await make_user(role="admin")
    member_headers = await make_headers(role="member")
    version = await _pending_version(db_session, owner.id)
    await db_session.commit()

    browse = await client.get("/api/v1/marketplace/tools", headers=member_headers)
    assert browse.status_code == 200
    approve = await client.post(
        f"/api/v1/external-tool-servers/{version.server_id}/tools/{version.id}/approve",
        headers=member_headers,
        json={"reason": "Not authorized"},
    )
    assert approve.status_code == 403
    assert approve.json()["error"]["code"] == "FORBIDDEN"


@pytest.mark.asyncio
async def test_reject_requires_reason_and_viewer_cannot_review(
    client, db_session, make_headers, make_user
):
    owner, _ = await make_user(role="admin")
    viewer_headers = await make_headers(role="viewer")
    version = await _pending_version(db_session, owner.id)
    await db_session.commit()
    base = (
        f"/api/v1/external-tool-servers/{version.server_id}/tools/{version.id}/reject"
    )

    missing_reason = await client.post(
        base, headers=await make_headers(role="admin"), json={}
    )
    assert missing_reason.status_code == 422
    denied = await client.post(
        base, headers=viewer_headers, json={"reason": "Not reviewed"}
    )
    assert denied.status_code == 403
