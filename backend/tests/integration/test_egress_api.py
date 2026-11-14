"""002 出站 API 集成覆盖：规则 CRUD、权限、重复项与内置保护。"""

from __future__ import annotations

import uuid

from app.models.egress import EgressAccess

AGENTS_URL = "/api/v1/agents"
ADMIN_BLACKLIST_URL = "/api/v1/admin/egress-blacklist"


async def _create_agent(client, headers):
    response = await client.post(
        AGENTS_URL,
        headers=headers,
        json={
            "name": f"egress-{uuid.uuid4().hex[:8]}",
            "system_prompt": "test",
            "model": "gpt-4o-mini",
            "tools": [],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_agent_egress_rules_crud_permissions_and_duplicate(
    client, make_headers
):
    owner_headers = await make_headers(role="member")
    agent = await _create_agent(client, owner_headers)
    endpoint = f"{AGENTS_URL}/{agent['id']}/egress-rules"
    rule_data = {
        "target_type": "domain",
        "target": "api.example.test",
        "port_range": "443",
        "note": "test endpoint",
    }

    created = await client.post(endpoint, headers=owner_headers, json=rule_data)
    assert created.status_code == 201, created.text
    rule = created.json()
    assert rule["action"] == "allow"
    assert rule["target"] == "api.example.test"
    assert rule["port_range"] == "443"

    duplicate = await client.post(endpoint, headers=owner_headers, json=rule_data)
    assert duplicate.status_code == 409

    listed = await client.get(endpoint, headers=owner_headers)
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()["items"]] == [rule["id"]]

    other_headers = await make_headers(role="member")
    forbidden_list = await client.get(endpoint, headers=other_headers)
    assert forbidden_list.status_code == 403
    forbidden_create = await client.post(endpoint, headers=other_headers, json=rule_data)
    assert forbidden_create.status_code == 403
    forbidden_delete = await client.delete(f"{endpoint}/{rule['id']}", headers=other_headers)
    assert forbidden_delete.status_code == 403

    deleted = await client.delete(f"{endpoint}/{rule['id']}", headers=owner_headers)
    assert deleted.status_code == 204
    assert (await client.get(endpoint, headers=owner_headers)).json()["items"] == []


async def test_builtin_egress_blacklist_is_read_only_and_admin_global_crud(
    client, make_headers
):
    member_headers = await make_headers(role="member")
    forbidden = await client.get(ADMIN_BLACKLIST_URL, headers=member_headers)
    assert forbidden.status_code == 403

    admin_headers = await make_headers(role="admin")
    listed = await client.get(ADMIN_BLACKLIST_URL, headers=admin_headers)
    assert listed.status_code == 200
    builtin = next(item for item in listed.json()["items"] if item["builtin"])
    blocked = await client.delete(
        f"{ADMIN_BLACKLIST_URL}/{builtin['id']}", headers=admin_headers
    )
    assert blocked.status_code == 400

    payload = {
        "target_type": "domain",
        "target": f"blocked-{uuid.uuid4().hex[:8]}.example.test",
        "port_range": "443",
        "note": "integration test",
    }
    created = await client.post(ADMIN_BLACKLIST_URL, headers=admin_headers, json=payload)
    assert created.status_code == 201, created.text
    rule_id = created.json()["id"]
    duplicate = await client.post(ADMIN_BLACKLIST_URL, headers=admin_headers, json=payload)
    assert duplicate.status_code == 409
    deleted = await client.delete(f"{ADMIN_BLACKLIST_URL}/{rule_id}", headers=admin_headers)
    assert deleted.status_code == 204


async def test_egress_accesses_are_visible_by_execution_and_admin(
    client, make_headers, make_user, sandbox_factory, db_session
):
    owner, _ = await make_user()
    owner_headers = await make_headers(user=owner)
    execution = await sandbox_factory(owner=owner, status="ok", exit_code=0)
    record = EgressAccess(
        run_id=execution.run_id,
        execution_id=execution.id,
        agent_id=execution.agent_id,
        user_id=owner.id,
        target_ip="203.0.113.10",
        target_domain="api.example.test",
        port=443,
        protocol="tcp",
        action="allow",
        bytes_out=12,
    )
    db_session.add(record)
    await db_session.commit()

    by_execution = await client.get(
        f"/api/v1/runs/{execution.run_id}/executions/{execution.id}/egress",
        headers=owner_headers,
    )
    assert by_execution.status_code == 200, by_execution.text
    assert by_execution.json()["items"] == [
        {
            "target_ip": "203.0.113.10",
            "target_domain": "api.example.test",
            "port": 443,
            "action": "allow",
            "matched_rule_id": None,
            "bytes_out": 12,
            "occurred_at": record.occurred_at.isoformat().replace("+00:00", "Z"),
        }
    ]

    admin_headers = await make_headers(role="admin")
    accesses = await client.get(
        "/api/v1/admin/egress-accesses", headers=admin_headers, params={"run_id": execution.run_id}
    )
    assert accesses.status_code == 200
    assert accesses.json()["total"] == 1
    assert accesses.json()["items"][0]["target_domain"] == "api.example.test"

    other_headers = await make_headers(role="member")
    hidden = await client.get(
        f"/api/v1/runs/{execution.run_id}/executions/{execution.id}/egress",
        headers=other_headers,
    )
    assert hidden.status_code == 404
