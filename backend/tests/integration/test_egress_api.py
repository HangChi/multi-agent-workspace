"""002 出站 API 集成覆盖：规则 CRUD、权限、重复项与内置保护。"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.api.errors import AppError
from app.models.audit_log import AuditLog
from app.models.egress import EgressAccess, EgressRule

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


async def test_agent_rule_delete_audits_actor_agent_rule_and_target(
    client, auth_headers, make_headers, db_session
):
    headers, owner = await auth_headers()
    agent = await _create_agent(client, headers)
    endpoint = f"{AGENTS_URL}/{agent['id']}/egress-rules"
    created = await client.post(
        endpoint, headers=headers,
        json={"target_type": "domain", "target": "audit.example.test", "port_range": "443"},
    )
    assert created.status_code == 201, created.text
    rule = created.json()
    url = f"{endpoint}/{rule['id']}"
    audit_query = select(AuditLog).where(
        AuditLog.action == "egress_rule_delete", AuditLog.target_id == agent["id"]
    )

    forbidden = await client.delete(url, headers=await make_headers())
    assert forbidden.status_code == 403
    assert (await db_session.execute(audit_query)).scalars().all() == []

    assert (await client.delete(url, headers=headers)).status_code == 204
    audit = (await db_session.execute(audit_query)).scalar_one()
    assert audit.user_id == owner.id
    assert audit.target_type == "agent"
    assert audit.detail == {
        "agent_id": agent["id"],
        "rule_id": rule["id"],
        "target_type": "domain",
        "target": "audit.example.test",
        "port_range": "443",
    }
    assert await db_session.get(EgressRule, rule["id"]) is None
    assert (await client.delete(url, headers=headers)).status_code == 404
    assert len((await db_session.execute(audit_query)).scalars().all()) == 1


@pytest.mark.parametrize("audit_failure", ["raise", "sqlalchemy"])
async def test_agent_rule_delete_rolls_back_when_audit_fails(
    client, make_headers, db_session, monkeypatch, audit_failure
):
    from app.api.v1 import agent_egress
    from app.core.audit import write_audit

    headers = await make_headers()
    agent = await _create_agent(client, headers)
    endpoint = f"{AGENTS_URL}/{agent['id']}/egress-rules"
    created = await client.post(
        endpoint, headers=headers,
        json={"target_type": "domain", "target": "rollback.example.test"},
    )
    assert created.status_code == 201, created.text
    rule_id = created.json()["id"]

    async def fail_audit(*args, **kwargs):
        if audit_failure == "sqlalchemy":
            # 真实 write_audit 的 FK 错误：必须回滚并向路由传播，不能吞错后返回 204。
            kwargs["user_id"] = str(uuid.uuid4())
            return await write_audit(*args, **kwargs)
        raise AppError("INVALID_INPUT", "audit unavailable")

    monkeypatch.setattr(agent_egress, "write_audit", fail_audit)
    response = await client.delete(f"{endpoint}/{rule_id}", headers=headers)
    assert response.status_code == 400
    assert await db_session.get(EgressRule, rule_id) is not None
    audits = await db_session.execute(
        select(AuditLog).where(
            AuditLog.action == "egress_rule_delete", AuditLog.target_id == agent["id"]
        )
    )
    assert audits.scalars().all() == []


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
