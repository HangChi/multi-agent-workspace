"""Agent 组集成测试（T030）：创建/成员增删/排序/级联删除/归属校验。

覆盖：
- 创建 201（routing_strategy 默认 fan_out）
- 添加成员 201 / 重复成员 409 / 位置冲突 409
- 移除成员 204
- 排序保存（交换 position）
- 删除组级联清空成员 204
- 非 owner 操作 403
"""

GROUPS_URL = "/api/v1/groups"


async def _create_group(client, headers: dict, **overrides: str) -> dict:
    payload = {"name": "test-group", "description": "a group", "routing_strategy": "fan_out"}
    payload.update(overrides)
    resp = await client.post(GROUPS_URL, headers=headers, json=payload)
    assert resp.status_code == 201
    return resp.json()


async def _create_agent(client, headers: dict, name: str = "agent") -> dict:
    resp = await client.post(
        "/api/v1/agents",
        headers=headers,
        json={
            "name": name,
            "system_prompt": "you are a helpful assistant",
            "model": "gpt-4o-mini",
        },
    )
    assert resp.status_code == 201
    return resp.json()


async def _add_member(client, headers: dict, group_id: str, agent_id: str, position: int):
    return await client.post(
        f"{GROUPS_URL}/{group_id}/members",
        headers=headers,
        json={"agent_id": agent_id, "position": position},
    )


# ---------- 创建 ----------


async def test_create_group_default_fan_out(client, make_headers):
    """创建组：routing_strategy 未传 → 默认 fan_out。"""
    headers = await make_headers(role="member")
    payload = {"name": "g1", "description": "desc"}
    resp = await client.post(GROUPS_URL, headers=headers, json=payload)
    assert resp.status_code == 201
    body = resp.json()
    assert body["routing_strategy"] == "fan_out"
    assert body["status"] == "active"
    assert body["members"] == []


async def test_create_group_invalid_strategy(client, make_headers):
    """非法 routing_strategy → 422。"""
    headers = await make_headers(role="member")
    resp = await client.post(
        GROUPS_URL,
        headers=headers,
        json={"name": "g1", "routing_strategy": "teleport"},
    )
    assert resp.status_code == 422


# ---------- 成员管理 ----------


async def test_add_member_success(client, make_headers):
    """添加成员 → 201，position 生效。"""
    headers = await make_headers(role="member")
    group = await _create_group(client, headers)
    agent = await _create_agent(client, headers)

    resp = await _add_member(client, headers, group["id"], agent["id"], 1)
    assert resp.status_code == 201
    member = resp.json()
    assert member["agent_id"] == agent["id"]
    assert member["position"] == 1


async def test_add_member_duplicate_conflict(client, make_headers):
    """同一 Agent 重复添加 → 409。"""
    headers = await make_headers(role="member")
    group = await _create_group(client, headers)
    agent = await _create_agent(client, headers)

    await _add_member(client, headers, group["id"], agent["id"], 1)
    resp = await _add_member(client, headers, group["id"], agent["id"], 2)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "CONFLICT"


async def test_add_member_position_conflict(client, make_headers):
    """position 冲突 → 409。"""
    headers = await make_headers(role="member")
    group = await _create_group(client, headers)
    a1 = await _create_agent(client, headers, name="a1")
    a2 = await _create_agent(client, headers, name="a2")

    await _add_member(client, headers, group["id"], a1["id"], 1)
    resp = await _add_member(client, headers, group["id"], a2["id"], 1)
    assert resp.status_code == 409


async def test_add_member_archived_agent(client, make_headers):
    """归档 Agent 不可加入组 → 400。"""
    headers = await make_headers(role="member")
    group = await _create_group(client, headers)
    agent = await _create_agent(client, headers)
    await client.post(f"/api/v1/agents/{agent['id']}/archive", headers=headers)

    resp = await _add_member(client, headers, group["id"], agent["id"], 1)
    assert resp.status_code == 400


async def test_remove_member(client, make_headers):
    """移除成员 → 204，随后列表不含该成员。"""
    headers = await make_headers(role="member")
    group = await _create_group(client, headers)
    agent = await _create_agent(client, headers)
    await _add_member(client, headers, group["id"], agent["id"], 1)

    resp = await client.delete(
        f"{GROUPS_URL}/{group['id']}/members/{agent['id']}", headers=headers
    )
    assert resp.status_code == 204

    detail = await client.get(f"{GROUPS_URL}/{group['id']}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["members"] == []


async def test_reorder_members(client, make_headers):
    """排序保存：交换两个成员的 position。"""
    headers = await make_headers(role="member")
    group = await _create_group(client, headers)
    a1 = await _create_agent(client, headers, name="a1")
    a2 = await _create_agent(client, headers, name="a2")
    await _add_member(client, headers, group["id"], a1["id"], 1)
    await _add_member(client, headers, group["id"], a2["id"], 2)

    # 交换：a1 → 2，a2 → 1
    resp = await client.patch(
        f"{GROUPS_URL}/{group['id']}/members",
        headers=headers,
        json={"positions": [{"agent_id": a2["id"], "position": 1}, {"agent_id": a1["id"], "position": 2}]},
    )
    assert resp.status_code == 200

    detail = await client.get(f"{GROUPS_URL}/{group['id']}", headers=headers)
    members = detail.json()["members"]
    assert members[0]["agent_id"] == a2["id"]
    assert members[1]["agent_id"] == a1["id"]


# ---------- 删除组 ----------


async def test_delete_group_cascades_members(client, make_headers, db_session):
    """删除组 → 204，组内成员记录级联清空。"""
    from sqlalchemy import select

    from app.models.group_member import GroupMember

    headers = await make_headers(role="member")
    group = await _create_group(client, headers)
    agent = await _create_agent(client, headers)
    await _add_member(client, headers, group["id"], agent["id"], 1)

    resp = await client.delete(f"{GROUPS_URL}/{group['id']}", headers=headers)
    assert resp.status_code == 204

    rows = (
        await db_session.execute(
            select(GroupMember).where(GroupMember.group_id == group["id"])
        )
    ).scalars().all()
    assert rows == []


# ---------- 归属校验 ----------


async def test_group_operations_non_owner_forbidden(client, auth_headers):
    """非 owner 操作他人组 → 403（含成员增删）。"""
    owner_headers, _ = await auth_headers(role="member")
    group = await _create_group(client, owner_headers)
    agent = await _create_agent(client, owner_headers)
    await _add_member(client, owner_headers, group["id"], agent["id"], 1)

    other_headers, _ = await auth_headers(role="member")
    # 编辑组
    resp = await client.patch(
        f"{GROUPS_URL}/{group['id']}",
        headers=other_headers,
        json={"name": "hacked"},
    )
    assert resp.status_code == 403
    # 加成员
    other_agent = await _create_agent(client, other_headers, name="other-agent")
    resp = await _add_member(client, other_headers, group["id"], other_agent["id"], 2)
    assert resp.status_code == 403
    # 删成员
    resp = await client.delete(
        f"{GROUPS_URL}/{group['id']}/members/{agent['id']}", headers=other_headers
    )
    assert resp.status_code == 403
    # 删组
    resp = await client.delete(f"{GROUPS_URL}/{group['id']}", headers=other_headers)
    assert resp.status_code == 403


async def test_group_add_member_other_agent_forbidden(client, auth_headers):
    """把自己的 Agent 加入他人组 → 403（归属校验双向）。"""
    owner_headers, _ = await auth_headers(role="member")
    group = await _create_group(client, owner_headers)

    other_headers, _ = await auth_headers(role="member")
    other_agent = await _create_agent(client, other_headers, name="my-agent")
    resp = await _add_member(client, other_headers, group["id"], other_agent["id"], 1)
    assert resp.status_code == 403


async def test_list_groups_with_members(client, make_headers):
    """GET /groups 返回 GroupWithMembers（含成员展开）。"""
    headers = await make_headers(role="member")
    group = await _create_group(client, headers)
    agent = await _create_agent(client, headers, name="member-agent")
    await _add_member(client, headers, group["id"], agent["id"], 1)

    resp = await client.get(GROUPS_URL, headers=headers)
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    members = items[0]["members"]
    assert len(members) == 1
    assert members[0]["agent_id"] == agent["id"]
    assert members[0]["name"] == "member-agent"
    assert members[0]["position"] == 1
