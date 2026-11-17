"""事务边界回归（docs/code-audit-2026-11-17.md #9）。

锁住三件事，任何一条退化都说明「业务一次提交 + 审计一次提交」又回来了：

1. 一个写请求只 `commit` 一次（`Session.after_commit` 计数）；
2. 审计写失败时业务不落库（审计与业务同生共死）；
3. `assign` 的 Celery 入队发生在提交之后（否则 Worker 可能读不到 Run）。
"""

import uuid

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.api.errors import AppError
from app.models.agent import Agent
from app.models.audit_log import AuditLog
from app.models.run import Run
from app.services import assignment_service

AGENTS_URL = "/api/v1/agents"
REQUESTS_URL = "/api/v1/requests"
GROUPS_URL = "/api/v1/groups"

AGENT_PAYLOAD = {
    "name": "tx-agent",
    "description": "transaction boundary agent",
    "system_prompt": "you are a helpful assistant",
    "model": "gpt-4o-mini",
}
REQUEST_PAYLOAD = {
    "title": "tx request",
    "description": "transaction boundary request",
    "expected_output": "result",
    "priority": "normal",
}


@pytest.fixture
def commits():
    """统计 `Session.after_commit` 次数。

    async session 底层仍是 SyncSession，事件对两者都生效；计数在发出请求前
    清零，因此只统计该请求自身产生的提交。
    """
    counter = {"n": 0}

    def _count(_session: Session) -> None:
        counter["n"] += 1

    event.listen(Session, "after_commit", _count)
    try:
        yield counter
    finally:
        event.remove(Session, "after_commit", _count)


async def _audits(db_session, action: str, target_id: str) -> list[AuditLog]:
    rows = await db_session.execute(
        select(AuditLog).where(
            AuditLog.action == action, AuditLog.target_id == target_id
        )
    )
    return list(rows.scalars().all())


async def _create_agent(client, headers: dict, name: str = "tx-agent") -> dict:
    payload = dict(AGENT_PAYLOAD, name=f"{name}-{uuid.uuid4().hex[:6]}")
    resp = await client.post(AGENTS_URL, headers=headers, json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def test_create_agent_commits_once_and_audits_same_transaction(
    client, make_headers, db_session, commits
):
    headers = await make_headers()
    payload = dict(AGENT_PAYLOAD, name=f"tx-{uuid.uuid4().hex[:6]}")

    commits["n"] = 0
    resp = await client.post(AGENTS_URL, headers=headers, json=payload)

    assert resp.status_code == 201, resp.text
    assert commits["n"] == 1, "写端点必须只提交一次（业务 + 审计）"
    assert len(await _audits(db_session, "agent_create", resp.json()["id"])) == 1


async def test_update_and_delete_agent_commit_once(
    client, make_headers, db_session, commits
):
    headers = await make_headers()
    agent = await _create_agent(client, headers)

    commits["n"] = 0
    patched = await client.patch(
        f"{AGENTS_URL}/{agent['id']}", headers=headers, json={"name": "renamed-tx"}
    )
    assert patched.status_code == 200, patched.text
    assert commits["n"] == 1
    assert len(await _audits(db_session, "agent_update", agent["id"])) == 1

    commits["n"] = 0
    deleted = await client.delete(f"{AGENTS_URL}/{agent['id']}", headers=headers)
    assert deleted.status_code == 204, deleted.text
    assert commits["n"] == 1
    assert len(await _audits(db_session, "agent_delete", agent["id"])) == 1


async def test_create_group_and_add_member_commit_once(
    client, make_headers, db_session, commits
):
    headers = await make_headers()
    agent = await _create_agent(client, headers)

    commits["n"] = 0
    group = await client.post(
        GROUPS_URL,
        headers=headers,
        json={
            "name": f"tx-group-{uuid.uuid4().hex[:6]}",
            "routing_strategy": "fan_out",
        },
    )
    assert group.status_code == 201, group.text
    assert commits["n"] == 1
    group_id = group.json()["id"]
    assert len(await _audits(db_session, "group_create", group_id)) == 1

    commits["n"] = 0
    member = await client.post(
        f"{GROUPS_URL}/{group_id}/members",
        headers=headers,
        json={"agent_id": agent["id"], "position": 1},
    )
    assert member.status_code == 201, member.text
    assert commits["n"] == 1
    assert len(await _audits(db_session, "group_member_change", group_id)) == 1


async def test_create_request_commits_once(client, make_headers, db_session, commits):
    headers = await make_headers()

    commits["n"] = 0
    resp = await client.post(REQUESTS_URL, headers=headers, json=REQUEST_PAYLOAD)

    assert resp.status_code == 201, resp.text
    assert commits["n"] == 1
    assert len(await _audits(db_session, "request_create", resp.json()["id"])) == 1


async def test_assign_commits_once_and_audits_same_transaction(
    client, make_headers, db_session, commits, monkeypatch
):
    """assign 现在自己不再提交：端点一次提交同时落地需求状态、Run 与审计。"""
    headers = await make_headers()
    agent = await _create_agent(client, headers)
    monkeypatch.setattr(assignment_service, "_enqueue", lambda _ids: [])

    created = await client.post(REQUESTS_URL, headers=headers, json=REQUEST_PAYLOAD)
    assert created.status_code == 201, created.text
    request_id = created.json()["id"]

    commits["n"] = 0
    resp = await client.post(
        f"{REQUESTS_URL}/{request_id}/assign",
        headers=headers,
        json={"assignee_type": "agent", "assignee_id": agent["id"]},
    )

    assert resp.status_code == 202, resp.text
    assert commits["n"] == 1
    runs = await db_session.execute(select(Run).where(Run.request_id == request_id))
    assert len(list(runs.scalars().all())) == 1
    assert len(await _audits(db_session, "assign", request_id)) == 1


async def test_assign_enqueues_only_after_commit(
    client, make_headers, commits, monkeypatch
):
    """入队在提交之后：spy 记录调用时的已完成提交次数，必须 ≥ 1。"""
    headers = await make_headers()
    agent = await _create_agent(client, headers)
    created = await client.post(REQUESTS_URL, headers=headers, json=REQUEST_PAYLOAD)
    request_id = created.json()["id"]

    seen: list[int] = []

    def _spy(_run_ids: list[str]) -> list:
        seen.append(commits["n"])
        return []

    monkeypatch.setattr(assignment_service, "_enqueue", _spy)

    commits["n"] = 0
    resp = await client.post(
        f"{REQUESTS_URL}/{request_id}/assign",
        headers=headers,
        json={"assignee_type": "agent", "assignee_id": agent["id"]},
    )
    assert resp.status_code == 202, resp.text
    assert seen, "入队函数必须被调用"
    assert seen[0] >= 1, "入队时业务必须已经提交"


async def test_audit_failure_keeps_business_out_of_db(
    client, make_headers, db_session, monkeypatch
):
    """审计写失败 → 整个事务回滚，业务行不落库且返回 5xx。"""
    from app.api.v1 import agents as agents_module

    headers = await make_headers()
    name = f"audit-fail-{uuid.uuid4().hex[:6]}"

    async def _boom(*_args, **_kwargs):
        raise AppError("INTERNAL_ERROR", "审计日志写入失败")

    monkeypatch.setattr(agents_module, "write_audit", _boom)
    resp = await client.post(
        AGENTS_URL, headers=headers, json=dict(AGENT_PAYLOAD, name=name)
    )

    assert resp.status_code >= 500, resp.text
    rows = await db_session.execute(select(Agent).where(Agent.name == name))
    assert list(rows.scalars().all()) == [], "审计失败时业务行不得落库"
