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
async def test_server_create_list_detail_update_and_validate(client, make_headers, monkeypatch):
    from app.tasks.validate_external_server import validate_external_server

    monkeypatch.setattr(validate_external_server, "delay", lambda server_id, requested_by: None)
    headers = await make_headers(role="admin")
    response = await client.post("/api/v1/external-tool-servers", headers=headers, json=SERVER)
    assert response.status_code == 201
    created = response.json()
    assert created["name"] == SERVER["name"]
    assert created["status"] == "pending_review"
    assert "canary-secret-value" not in response.text
    assert created["credentials"][0]["label"] == "team-search-prod"
    server_id = created["id"]

    listing = await client.get("/api/v1/external-tool-servers?q=Team%20Search", headers=headers)
    assert listing.status_code == 200
    assert listing.json()["total"] == 1
    assert "canary-secret-value" not in listing.text

    detail = await client.get(f"/api/v1/external-tool-servers/{server_id}", headers=headers)
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
async def test_server_registration_requires_admin_and_rejects_unsafe_endpoint(client, make_headers):
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
async def test_duplicate_normalized_endpoint_is_conflict(client, make_headers, monkeypatch):
    from app.tasks.validate_external_server import validate_external_server

    monkeypatch.setattr(validate_external_server, "delay", lambda server_id, requested_by: None)
    headers = await make_headers(role="admin")
    first = await client.post("/api/v1/external-tool-servers", headers=headers, json=SERVER)
    assert first.status_code == 201
    second = await client.post("/api/v1/external-tool-servers", headers=headers, json=SERVER)
    assert second.status_code == 409
    assert "canary-secret-value" not in second.text
