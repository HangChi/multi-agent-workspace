"""Contract coverage for registering and managing external MCP servers."""

import pytest

SERVER = {
    "name": "Team Search",
    "description": "Approved knowledge search",
    "endpoint_url": "https://8.8.8.8/mcp",
    "source_url": "https://git.example.test/team-search",
    "maintainer": "Platform Team",
    "auth_mode": "static_bearer",
    "credential": {
        "label": "team-search-prod",
        "kind": "bearer",
        "value": "canary-secret-value",
    },
}


@pytest.mark.asyncio
async def test_server_create_list_detail_update_and_validate(
    client, make_headers, monkeypatch
):
    from app.tasks.validate_external_server import validate_external_server

    monkeypatch.setattr(
        validate_external_server, "delay", lambda server_id, requested_by: None
    )
    headers = await make_headers(role="admin")
    response = await client.post(
        "/api/v1/external-tool-servers", headers=headers, json=SERVER
    )
    assert response.status_code == 201
    created = response.json()
    assert created["name"] == SERVER["name"]
    assert created["status"] == "pending_review"
    assert "canary-secret-value" not in response.text
    assert created["credentials"][0]["label"] == "team-search-prod"
    server_id = created["id"]

    listing = await client.get(
        "/api/v1/external-tool-servers?q=Team%20Search", headers=headers
    )
    assert listing.status_code == 200
    assert listing.json()["total"] == 1
    assert "canary-secret-value" not in listing.text

    detail = await client.get(
        f"/api/v1/external-tool-servers/{server_id}", headers=headers
    )
    assert detail.status_code == 200
    assert "canary-secret-value" not in detail.text

    updated = await client.patch(
        f"/api/v1/external-tool-servers/{server_id}",
        headers=headers,
        json={"name": "Team Search Updated"},
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "Team Search Updated"

    validation = await client.post(
        f"/api/v1/external-tool-servers/{server_id}/validate", headers=headers
    )
    assert validation.status_code == 202
    assert "canary-secret-value" not in validation.text


@pytest.mark.asyncio
async def test_server_registration_requires_admin_and_rejects_unsafe_endpoint(
    client, make_headers
):
    member_headers = await make_headers(role="member")
    denied = await client.post(
        "/api/v1/external-tool-servers", headers=member_headers, json=SERVER
    )
    assert denied.status_code == 403

    admin_headers = await make_headers(role="admin")
    unsafe = {**SERVER, "endpoint_url": "http://127.0.0.1:8080/mcp"}
    rejected = await client.post(
        "/api/v1/external-tool-servers", headers=admin_headers, json=unsafe
    )
    assert rejected.status_code == 400
    assert "canary-secret-value" not in rejected.text


@pytest.mark.asyncio
async def test_duplicate_normalized_endpoint_is_conflict(
    client, make_headers, monkeypatch
):
    from app.tasks.validate_external_server import validate_external_server

    monkeypatch.setattr(
        validate_external_server, "delay", lambda server_id, requested_by: None
    )
    headers = await make_headers(role="admin")
    first = await client.post(
        "/api/v1/external-tool-servers", headers=headers, json=SERVER
    )
    assert first.status_code == 201
    second = await client.post(
        "/api/v1/external-tool-servers", headers=headers, json=SERVER
    )
    assert second.status_code == 409
    assert "canary-secret-value" not in second.text


@pytest.mark.asyncio
async def test_server_detail_projection_reads_tool_listings_without_lazy_io(
    db_session, make_user
):
    """GET detail must not lazy-load a tool's listing.

    The server projection reads ``tool.listings`` for every discovered tool, so
    ``get_server`` has to eager-load that relationship. When it does not, the
    detail endpoint raises ``MissingGreenlet`` for any server that has tools --
    which is invisible to a test that builds the whole graph in one session,
    because SQLAlchemy then serves the relationship from the identity map.
    """
    from app.models.external_tool import (
        ExternalToolServer,
        ExternalToolVersion,
        MarketplaceListing,
    )
    from app.services import external_tool_service

    user, _ = await make_user(role="admin")
    server = ExternalToolServer(
        owner_id=user.id,
        created_by=user.id,
        name="Detail Projection Fixture",
        description="Fixture",
        endpoint_url="https://8.8.8.8/mcp",
        normalized_endpoint_url="https://8.8.8.8/mcp",
        maintainer="Test",
        auth_mode="none",
        status="pending_review",
    )
    db_session.add(server)
    await db_session.flush()
    tool = ExternalToolVersion(
        server_id=server.id,
        external_name="search",
        stable_name="mcp_search",
        version="1.0.0",
        description="Fixture tool",
        input_schema={"type": "object"},
        definition_hash="0" * 64,
        availability="valid",
    )
    db_session.add(tool)
    await db_session.flush()
    listing = MarketplaceListing(
        tool_version_id=tool.id,
        status="published",
        usage_summary="Fixture usage",
        data_handling="Fixture handling",
    )
    db_session.add(listing)
    await db_session.flush()
    listing_id = listing.id
    # Force the projection to read the relationship from the database rather
    # than from the objects this session just constructed.
    db_session.expunge_all()

    loaded = await external_tool_service.get_server(
        db_session, server_id=server.id, owner_id=user.id, is_admin=True
    )
    projection = external_tool_service.server_projection(loaded)

    assert len(projection["tools"]) == 1
    assert projection["tools"][0]["listing_id"] == listing_id
    assert projection["tools"][0]["listing_status"] == "published"


# ---------- 015 覆盖率补齐：get_server 越权/缺失 → 404 ----------


@pytest.mark.asyncio
async def test_get_server_missing_or_foreign_owner_is_not_found(db_session, make_user):
    """非 admin 访问不存在/他人的 server 一律 404，不暴露存在性。"""
    from app.api.errors import AppError
    from app.services import external_tool_service

    admin, _ = await make_user(role="admin")
    stranger, _ = await make_user(role="member")

    # 不存在的 server
    with pytest.raises(AppError) as exc:
        await external_tool_service.get_server(
            db_session,
            server_id="00000000-0000-0000-0000-000000000000",
            owner_id=admin.id,
            is_admin=False,
        )
    assert exc.value.code == "NOT_FOUND"

    # 存在但他人的 server（非 admin）→ 同样 404，不暴露存在性
    from app.models.external_tool import ExternalToolServer

    server = ExternalToolServer(
        owner_id=stranger.id,
        created_by=stranger.id,
        name="Foreign Server",
        description="Fixture",
        endpoint_url="https://8.8.8.8/mcp",
        normalized_endpoint_url="https://8.8.8.8/mcp",
        maintainer="Test",
        auth_mode="none",
        status="pending_review",
    )
    db_session.add(server)
    await db_session.flush()

    with pytest.raises(AppError) as exc:
        await external_tool_service.get_server(
            db_session,
            server_id=server.id,
            owner_id=admin.id,
            is_admin=False,
        )
    assert exc.value.code == "NOT_FOUND"
