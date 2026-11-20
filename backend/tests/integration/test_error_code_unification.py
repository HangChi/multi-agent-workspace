"""016 错误码语义统一：陌生人 → 404，参与者缺权限 → 403。

两类断言各有目的：

1. **状态码**：确认「非相关人访问」不再是 403，否则可以用 403/404 的差异
   枚举他人资源 id。
2. **响应体同构**（SC-003）：只断言状态码不够 —— 如果 404 的 message 写成
   「无权访问该需求」，存在性照样泄露。这里直接比对「真不存在」与「存在但
   不归你」的 JSON 是否相等。

白名单用例（viewer / 项目成员 / assignee）是防止把 403 一并改成 404 的护栏：
那些场景用户**知道**资源存在，回 404 会让他以为数据丢了。
"""

import uuid

import pytest

pytestmark = pytest.mark.integration

REQUESTS_URL = "/api/v1/requests"
AGENTS_URL = "/api/v1/agents"
GROUPS_URL = "/api/v1/groups"
PROJECTS_URL = "/api/v1/projects"


def _request_payload(**overrides: object) -> dict:
    payload = {
        "title": "错误码用例",
        "description": "someone else's request",
        "expected_output": "irrelevant",
        "priority": "normal",
    }
    payload.update(overrides)
    return payload


def _agent_payload(**overrides: object) -> dict:
    payload = {
        "name": f"agent-{uuid.uuid4().hex[:6]}",
        "description": "owned by A",
        "system_prompt": "you are helpful",
        "model": "gpt-4o-mini",
        "temperature": 0.7,
        "max_steps": 5,
        "max_run_seconds": 600,
        "tools": ["web_search"],
    }
    payload.update(overrides)
    return payload


async def _owned_request(client, headers) -> dict:
    resp = await client.post(REQUESTS_URL, json=_request_payload(), headers=headers)
    assert resp.status_code == 201, resp.text[:300]
    return resp.json()


async def _owned_agent(client, headers) -> dict:
    resp = await client.post(AGENTS_URL, json=_agent_payload(), headers=headers)
    assert resp.status_code == 201, resp.text[:300]
    return resp.json()


# ---------- US1：陌生人 → 404 ----------


async def test_foreign_request_is_not_found_on_read(client, make_headers):
    """FR-001：读他人需求 404，不能是 403。"""
    owner_headers = await make_headers()
    request = await _owned_request(client, owner_headers)

    stranger = await make_headers()
    resp = await client.get(f"{REQUESTS_URL}/{request['id']}", headers=stranger)
    assert resp.status_code == 404, resp.text[:200]


async def test_foreign_request_is_not_found_on_write(client, make_headers):
    """FR-001：写路径与子资源同样 404 —— 只改主端点等于没改。

    需求本身没有 DELETE 端点（405），所以这里用「改」和「读前置依赖」两条
    不同的路径验证：它们都要经过同一个归属校验。
    """
    owner_headers = await make_headers()
    request = await _owned_request(client, owner_headers)
    stranger = await make_headers()

    patch = await client.patch(
        f"{REQUESTS_URL}/{request['id']}", json={"title": "hijacked"}, headers=stranger
    )
    assert patch.status_code == 404, patch.text[:200]

    deps = await client.get(
        f"{REQUESTS_URL}/{request['id']}/dependencies", headers=stranger
    )
    assert deps.status_code == 404, deps.text[:200]

    dependents = await client.get(
        f"{REQUESTS_URL}/{request['id']}/dependents", headers=stranger
    )
    assert dependents.status_code == 404, dependents.text[:200]


async def test_foreign_and_missing_request_are_indistinguishable(client, make_headers):
    """SC-003/FR-008：两次响应必须完全相等，message 不能暗示「存在但不归你」。"""
    owner_headers = await make_headers()
    request = await _owned_request(client, owner_headers)
    stranger = await make_headers()

    foreign = await client.get(f"{REQUESTS_URL}/{request['id']}", headers=stranger)
    missing = await client.get(f"{REQUESTS_URL}/{uuid.uuid4()}", headers=stranger)

    assert foreign.status_code == missing.status_code == 404
    assert foreign.json() == missing.json()


async def test_foreign_agent_is_not_found(client, make_headers):
    """FR-002：他人 Agent 的读/改/删都是 404。"""
    owner_headers = await make_headers()
    agent = await _owned_agent(client, owner_headers)
    stranger = await make_headers()

    patch = await client.patch(
        f"{AGENTS_URL}/{agent['id']}", json={"name": "renamed"}, headers=stranger
    )
    assert patch.status_code == 404, patch.text[:200]

    delete = await client.delete(f"{AGENTS_URL}/{agent['id']}", headers=stranger)
    assert delete.status_code == 404, delete.text[:200]

    missing = await client.patch(
        f"{AGENTS_URL}/{uuid.uuid4()}", json={"name": "renamed"}, headers=stranger
    )
    assert patch.json() == missing.json()


async def test_foreign_group_is_not_found(client, make_headers):
    """FR-003：他人 Agent 组 404。"""
    owner_headers = await make_headers()
    created = await client.post(
        GROUPS_URL,
        json={"name": f"g-{uuid.uuid4().hex[:6]}", "description": "x"},
        headers=owner_headers,
    )
    assert created.status_code == 201, created.text[:200]
    group = created.json()

    stranger = await make_headers()
    resp = await client.get(f"{GROUPS_URL}/{group['id']}", headers=stranger)
    assert resp.status_code == 404, resp.text[:200]


async def test_foreign_egress_rule_write_is_not_found(client, make_headers):
    """FR-004：往他人 Agent 上加出网规则 404。"""
    owner_headers = await make_headers()
    agent = await _owned_agent(client, owner_headers)

    stranger = await make_headers()
    resp = await client.post(
        f"{AGENTS_URL}/{agent['id']}/egress-rules",
        json={
            "target_type": "domain",
            "target": "evil.example.test",
            "port_range": "443",
        },
        headers=stranger,
    )
    assert resp.status_code == 404, resp.text[:200]


async def test_adding_foreign_agent_to_group_is_not_found(client, make_headers):
    """FR-006：把他人 Agent 加进自己的组 404（不暴露该 Agent 存在）。"""
    owner_headers = await make_headers()
    agent = await _owned_agent(client, owner_headers)

    attacker_headers = await make_headers()
    created = await client.post(
        GROUPS_URL,
        json={"name": f"attacker-{uuid.uuid4().hex[:6]}", "description": "x"},
        headers=attacker_headers,
    )
    group = created.json()
    resp = await client.post(
        f"{GROUPS_URL}/{group['id']}/members",
        json={"agent_id": agent["id"], "position": 1},
        headers=attacker_headers,
    )
    assert resp.status_code == 404, resp.text[:200]


# ---------- US2：白名单仍是 403 ----------


async def test_viewer_role_still_forbidden(client, make_headers):
    """FR-007：角色不足与资源无关，必须保持 403。"""
    viewer = await make_headers(role="viewer")
    resp = await client.post(REQUESTS_URL, json=_request_payload(), headers=viewer)
    assert resp.status_code == 403, resp.text[:200]


async def test_project_member_cannot_manage_members(client, make_headers):
    """FR-007：成员知道项目存在、只是不是 owner → 403 而非 404。"""
    owner_headers = await make_headers()
    project = await client.post(
        PROJECTS_URL,
        json={"name": f"p-{uuid.uuid4().hex[:6]}", "description": "x"},
        headers=owner_headers,
    )
    assert project.status_code == 201, project.text[:200]
    project_id = project.json()["id"]

    member_user_headers = await make_headers()
    # 取成员自身 id：/auth/me 由 token 决定
    me = await client.get("/api/v1/auth/me", headers=member_user_headers)
    member_id = me.json()["id"]

    added = await client.post(
        f"{PROJECTS_URL}/{project_id}/members",
        json={"user_id": member_id},
        headers=owner_headers,
    )
    assert added.status_code == 201, added.text[:200]

    # 成员尝试再拉一个人进来 → 403（他知道项目存在，但没这个权限）
    other_headers = await make_headers()
    other_me = await client.get("/api/v1/auth/me", headers=other_headers)
    resp = await client.post(
        f"{PROJECTS_URL}/{project_id}/members",
        json={"user_id": other_me.json()["id"]},
        headers=member_user_headers,
    )
    assert resp.status_code == 403, resp.text[:200]
