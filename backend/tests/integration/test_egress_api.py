"""002 出站 API 集成覆盖：规则 CRUD、权限、重复项与内置保护。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

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


async def test_admin_access_time_window_is_half_open_and_summary_ignores_pagination(
    client, make_headers, make_user, sandbox_factory, db_session
):
    owner, _ = await make_user()
    execution = await sandbox_factory(owner=owner, status="ok", exit_code=0)
    start = datetime(2026, 1, 1, tzinfo=UTC)
    for minutes, action, domain in [
        (-1, "deny", "ip-direct"), (0, "allow", "api.example.test"),
        (30, "deny", "ip-direct"), (60, "allow", "ip-direct"),
        (61, "deny", "api.example.test"),
    ]:
        db_session.add(EgressAccess(
            run_id=execution.run_id, execution_id=execution.id, agent_id=execution.agent_id,
            user_id=owner.id, target_ip="203.0.113.10", target_domain=domain,
            port=443, protocol="tcp", action=action, occurred_at=start + timedelta(minutes=minutes),
        ))
    await db_session.commit()
    headers = await make_headers(role="admin")
    params = {
        "run_id": execution.run_id, "agent_id": execution.agent_id,
        "from": "2026-01-01T08:00:00+08:00", "to": "2026-01-01T01:00:00Z",
        "page_size": 1,
    }
    for page, expected_actions in [(1, ["deny"]), (2, ["allow"]), (3, [])]:
        response = await client.get(
            "/api/v1/admin/egress-accesses", headers=headers, params={**params, "page": page}
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["total"] == 2
        assert data["summary"] == {"allow": 1, "deny": 1, "ip_direct": 1}
        assert [item["action"] for item in data["items"]] == expected_actions
    allowed = await client.get(
        "/api/v1/admin/egress-accesses", headers=headers, params={**params, "action": "allow"}
    )
    assert allowed.json()["total"] == 1
    assert allowed.json()["summary"] == {"allow": 1, "deny": 0, "ip_direct": 0}
    for bound, expected_total in [("from", 4), ("to", 3)]:
        response = await client.get(
            "/api/v1/admin/egress-accesses", headers=headers,
            params={"run_id": execution.run_id, bound: params[bound]},
        )
        assert response.status_code == 200, response.text
        assert response.json()["total"] == expected_total


@pytest.mark.parametrize("bounds,status", [
    ({"from": "not-a-date"}, 422),
    ({"from": "2026-01-01T00:00:00"}, 422),
    ({"to": "2026-01-01T00:00:00"}, 422),
    ({"from": "2026-01-01T00:00:00Z", "to": "2026-01-01T00:00:00Z"}, 400),
    ({"from": "2026-01-01T01:00:00Z", "to": "2026-01-01T08:00:00+08:00"}, 400),
])
async def test_admin_access_time_window_rejects_invalid_bounds(client, make_headers, bounds, status):
    response = await client.get(
        "/api/v1/admin/egress-accesses", headers=await make_headers(role="admin"), params=bounds
    )
    assert response.status_code == status, response.text


async def test_restricted_agent_create_warns_without_preventing_save(client, make_headers):
    response = await client.post(
        AGENTS_URL, headers=await make_headers(),
        json={"name": "restricted-empty", "system_prompt": "test", "egress_mode": "restricted"},
    )
    assert response.status_code == 201, response.text
    assert response.json()["egress_mode"] == "restricted"
    assert "全部出站" in response.json()["warning"]


async def test_restricted_agent_save_warning_tracks_enabled_rules(
    client, make_headers, db_session
):
    headers = await make_headers()
    agent = await _create_agent(client, headers)
    assert agent["warning"] is None
    url = f"{AGENTS_URL}/{agent['id']}"
    response = await client.patch(url, headers=headers, json={"egress_mode": "restricted"})
    assert response.status_code == 200, response.text
    assert "全部出站" in response.json()["warning"]
    response = await client.patch(url, headers=headers, json={"name": "still-restricted"})
    assert "全部出站" in response.json()["warning"]
    rule_response = await client.post(
        f"{url}/egress-rules", headers=headers,
        json={"target_type": "ip", "target": "203.0.113.10"},
    )
    assert rule_response.status_code == 201, rule_response.text
    response = await client.patch(url, headers=headers, json={"egress_mode": "restricted"})
    assert response.json()["warning"] is None
    rule = await db_session.get(EgressRule, rule_response.json()["id"])
    rule.enabled = False
    await db_session.commit()
    response = await client.patch(url, headers=headers, json={"egress_mode": "restricted"})
    assert "全部出站" in response.json()["warning"]
    response = await client.patch(url, headers=headers, json={"egress_mode": "default"})
    assert response.json()["warning"] is None


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
