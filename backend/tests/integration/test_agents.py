"""Agent 集成测试（T029）：创建/编辑/归档/删除/组引用/归属校验。

覆盖 SC-005「无权限 100% 拒绝」与资源生命周期：
- 创建 201（member/admin；viewer 403 已在 test_permissions 覆盖）
- 编辑 200 / 非 owner 编辑 403
- 归档 200，归档后不出现在可分配列表
- 删除 204（无组引用）/ 被组引用删除 409（仅可归档）
"""

AGENTS_URL = "/api/v1/agents"


def _agent_payload(**overrides: str) -> dict:
    payload = {
        "name": "test-agent",
        "description": "a test agent",
        "system_prompt": "you are a helpful assistant",
        "model": "gpt-4o-mini",
        "temperature": 0.7,
        "max_steps": 10,
        "max_run_seconds": 1800,
        "tools": ["web_search"],
        "cost_limit_cents": 10000,
    }
    payload.update(overrides)
    return payload


async def _create_agent(client, headers: dict, **overrides: str) -> dict:
    resp = await client.post(AGENTS_URL, headers=headers, json=_agent_payload(**overrides))
    assert resp.status_code == 201
    return resp.json()


# ---------- 创建 ----------


async def test_create_agent_success(client, make_headers):
    """member 创建 Agent → 201，返回完整字段。"""
    headers = await make_headers(role="member")
    agent = await _create_agent(client, headers, name="my-agent")
    assert agent["id"]
    assert agent["name"] == "my-agent"
    assert agent["status"] == "active"
    assert agent["tools"] == ["web_search"]
    assert agent["owner_id"]


async def test_create_agent_invalid_tools(client, make_headers):
    """tools 不在白名单 → 422（schema 校验）。"""
    headers = await make_headers(role="member")
    # tools 传了字符串而非 list → Pydantic 422
    resp = await client.post(
        AGENTS_URL,
        headers=headers,
        json=_agent_payload(tools='["evil_tool"]'),
    )
    assert resp.status_code == 422

    # 白名单外的工具名（合法 list，非法内容）→ 422
    resp = await client.post(
        AGENTS_URL,
        headers=headers,
        json=_agent_payload(tools=["evil_tool"]),
    )
    assert resp.status_code == 422


# ---------- 编辑 ----------


async def test_update_agent_success(client, make_headers):
    """owner 编辑自己的 Agent → 200。"""
    headers = await make_headers(role="member")
    agent = await _create_agent(client, headers, name="before")
    resp = await client.patch(
        f"{AGENTS_URL}/{agent['id']}",
        headers=headers,
        json={"name": "after", "temperature": 0.2},
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "after"
    assert resp.json()["temperature"] == 0.2


async def test_update_agent_non_owner_forbidden(client, auth_headers):
    """非 owner 编辑他人 Agent → 403（归属校验）。"""
    owner_headers, _ = await auth_headers(role="member")
    agent = await _create_agent(client, owner_headers, name="owner-agent")

    other_headers, _ = await auth_headers(role="member")
    resp = await client.patch(
        f"{AGENTS_URL}/{agent['id']}",
        headers=other_headers,
        json={"name": "hacked"},
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "FORBIDDEN"


async def test_update_agent_not_found(client, make_headers):
    """编辑不存在的 Agent → 404。"""
    headers = await make_headers(role="member")
    resp = await client.patch(
        f"{AGENTS_URL}/00000000-0000-0000-0000-000000000000",
        headers=headers,
        json={"name": "x"},
    )
    assert resp.status_code == 404


# ---------- 归档 ----------


async def test_archive_agent(client, make_headers):
    """owner 归档自己的 Agent → 200 status=archived。"""
    headers = await make_headers(role="member")
    agent = await _create_agent(client, headers)
    resp = await client.post(f"{AGENTS_URL}/{agent['id']}/archive", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "archived"


async def test_archive_non_owner_forbidden(client, auth_headers):
    """非 owner 归档他人 Agent → 403。"""
    owner_headers, _ = await auth_headers(role="member")
    agent = await _create_agent(client, owner_headers)

    other_headers, _ = await auth_headers(role="member")
    resp = await client.post(f"{AGENTS_URL}/{agent['id']}/archive", headers=other_headers)
    assert resp.status_code == 403


async def test_archived_agent_not_in_assignable_list(client, make_headers):
    """归档 Agent 不出现在可分配列表（status=active 过滤）。"""
    headers = await make_headers(role="member")
    a1 = await _create_agent(client, headers, name="keep-me")
    a2 = await _create_agent(client, headers, name="archive-me")
    await client.post(f"{AGENTS_URL}/{a2['id']}/archive", headers=headers)

    resp = await client.get(f"{AGENTS_URL}?status=active", headers=headers)
    assert resp.status_code == 200
    ids = [item["id"] for item in resp.json()["items"]]
    assert a1["id"] in ids
    assert a2["id"] not in ids


# ---------- 删除 ----------


async def test_delete_agent_without_group_ref(client, make_headers):
    """无组引用的 Agent 删除 → 204。"""
    headers = await make_headers(role="member")
    agent = await _create_agent(client, headers)
    resp = await client.delete(f"{AGENTS_URL}/{agent['id']}", headers=headers)
    assert resp.status_code == 204


async def test_delete_agent_with_group_ref_conflict(client, make_headers, db_session):
    """被组引用的 Agent 删除 → 409（仅可归档）。"""
    from app.models.group import AgentGroup
    from app.models.group_member import GroupMember

    headers = await make_headers(role="member")
    agent = await _create_agent(client, headers)

    # 直接建组引用（组管理在 T030 覆盖）
    group = AgentGroup(owner_id=agent["owner_id"], name="g1", routing_strategy="fan_out")
    db_session.add(group)
    await db_session.flush()
    db_session.add(
        GroupMember(group_id=group.id, agent_id=agent["id"], position=1)
    )
    await db_session.commit()

    resp = await client.delete(f"{AGENTS_URL}/{agent['id']}", headers=headers)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "CONFLICT"

    # 归档仍允许
    resp = await client.post(f"{AGENTS_URL}/{agent['id']}/archive", headers=headers)
    assert resp.status_code == 200


# ---------- 列表过滤 ----------


async def test_list_agents_own_only(client, auth_headers):
    """列表只返回自己的 Agent（他人不可见）。"""
    headers_a, _ = await auth_headers(role="member")
    await _create_agent(client, headers_a, name="mine")

    headers_b, _ = await auth_headers(role="member")
    await _create_agent(client, headers_b, name="other")

    resp = await client.get(AGENTS_URL, headers=headers_a)
    assert resp.status_code == 200
    names = [item["name"] for item in resp.json()["items"]]
    assert "mine" in names
    assert "other" not in names


async def test_list_agents_search(client, make_headers):
    """?q= 关键词过滤。"""
    headers = await make_headers(role="member")
    await _create_agent(client, headers, name="translation-agent")
    await _create_agent(client, headers, name="coding-agent")

    resp = await client.get(f"{AGENTS_URL}?q=trans", headers=headers)
    assert resp.status_code == 200
    names = [item["name"] for item in resp.json()["items"]]
    assert names == ["translation-agent"]
