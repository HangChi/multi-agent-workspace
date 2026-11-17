"""列表端点分页测试。

覆盖 GET /agents、GET /groups、GET /agents/{id}/egress-rules：
- total 是**过滤后全量条数**，不是当页长度（否则前端分页器永远 1 页）
- page/page_size 真的生效，末页可短
- page 越界返回空 items 但仍给出正确 total
- ?all=true 返回全集（看板当拖拽目标、规则表单列全清单都靠它）
- page_size 非法（0 / 超过上限）→ 422
"""

import uuid

AGENTS_URL = "/api/v1/agents"
GROUPS_URL = "/api/v1/groups"


def _agent_payload(name: str) -> dict:
    return {
        "name": name,
        "description": "pagination probe",
        "system_prompt": "you are a helpful assistant",
        "model": "gpt-4o-mini",
        "temperature": 0.7,
        "max_steps": 10,
        "max_run_seconds": 1800,
        "tools": ["web_search"],
        "cost_limit_cents": 1000,
    }


async def _seed_agents(client, headers: dict, count: int) -> list[dict]:
    created = []
    for i in range(count):
        resp = await client.post(
            AGENTS_URL, headers=headers, json=_agent_payload(f"page-agent-{i}-{uuid.uuid4().hex[:6]}")
        )
        assert resp.status_code == 201, resp.text
        created.append(resp.json())
    return created


# ---------- /agents ----------


async def test_agents_pagination_total_is_filtered_count_not_page_length(client, make_headers):
    """total 必须是全量条数：返回当页长度会让前端分页器永远只有一页。"""
    headers = await make_headers(role="member")
    await _seed_agents(client, headers, 5)

    first = await client.get(AGENTS_URL, headers=headers, params={"page": 1, "page_size": 2})
    assert first.status_code == 200, first.text
    data = first.json()
    assert len(data["items"]) == 2
    # 关键断言：total 是 5 而不是 2
    assert data["total"] == 5

    third = await client.get(AGENTS_URL, headers=headers, params={"page": 3, "page_size": 2})
    third_data = third.json()
    assert len(third_data["items"]) == 1  # 末页只剩 1 条
    assert third_data["total"] == 5


async def test_agents_pagination_pages_do_not_overlap(client, make_headers):
    """逐页取到的 id 集合应无重叠，且并集等于全集。"""
    headers = await make_headers(role="member")
    seeded = await _seed_agents(client, headers, 4)
    seeded_ids = {a["id"] for a in seeded}

    seen: list[str] = []
    for page in (1, 2):
        resp = await client.get(AGENTS_URL, headers=headers, params={"page": page, "page_size": 2})
        seen.extend(item["id"] for item in resp.json()["items"])

    assert len(seen) == len(set(seen)), "分页出现重复项"
    assert set(seen) == seeded_ids


async def test_agents_page_beyond_last_returns_empty_but_keeps_total(client, make_headers):
    """越界页返回空 items，但 total 仍须正确 —— 前端靠它判断边界。"""
    headers = await make_headers(role="member")
    await _seed_agents(client, headers, 2)

    resp = await client.get(AGENTS_URL, headers=headers, params={"page": 99, "page_size": 20})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["items"] == []
    assert data["total"] == 2


async def test_agents_all_param_returns_every_item(client, make_headers):
    """?all=true 返回全集：看板需要完整拖拽目标集，不能被分页截断。"""
    headers = await make_headers(role="member")
    await _seed_agents(client, headers, 5)

    paged = await client.get(AGENTS_URL, headers=headers, params={"page": 1, "page_size": 2})
    assert len(paged.json()["items"]) == 2

    full = await client.get(AGENTS_URL, headers=headers, params={"all": "true"})
    assert full.status_code == 200, full.text
    data = full.json()
    assert len(data["items"]) == 5
    assert data["total"] == 5


async def test_agents_pagination_respects_status_filter(client, make_headers):
    """total 应是**过滤后**的条数，不是全部条数。"""
    headers = await make_headers(role="member")
    agents = await _seed_agents(client, headers, 3)
    # 归档其中一个
    archived = await client.post(
        f"{AGENTS_URL}/{agents[0]['id']}/archive", headers=headers
    )
    assert archived.status_code == 200, archived.text

    active = await client.get(AGENTS_URL, headers=headers, params={"status": "active"})
    assert active.json()["total"] == 2

    archived_list = await client.get(AGENTS_URL, headers=headers, params={"status": "archived"})
    assert archived_list.json()["total"] == 1

    everything = await client.get(AGENTS_URL, headers=headers)
    assert everything.json()["total"] == 3


async def test_agents_pagination_rejects_invalid_page_size(client, make_headers):
    """page_size 越界应 422，而不是悄悄按默认值处理。"""
    headers = await make_headers(role="member")
    for bad in ({"page": 0}, {"page_size": 0}, {"page_size": 101}):
        resp = await client.get(AGENTS_URL, headers=headers, params=bad)
        assert resp.status_code == 422, f"{bad} 应被拒绝，实际 {resp.status_code}"


# ---------- /groups ----------


async def test_groups_pagination_total_is_full_count(client, make_headers):
    headers = await make_headers(role="member")
    for i in range(3):
        resp = await client.post(
            GROUPS_URL, headers=headers, json={"name": f"grp-{i}-{uuid.uuid4().hex[:6]}"}
        )
        assert resp.status_code == 201, resp.text

    page1 = await client.get(GROUPS_URL, headers=headers, params={"page": 1, "page_size": 2})
    assert page1.status_code == 200, page1.text
    data = page1.json()
    assert len(data["items"]) == 2
    assert data["total"] == 3

    page2 = await client.get(GROUPS_URL, headers=headers, params={"page": 2, "page_size": 2})
    assert len(page2.json()["items"]) == 1
    assert page2.json()["total"] == 3


async def test_groups_all_param_returns_every_item(client, make_headers):
    """看板把组当拖拽目标，必须能拿到全集。"""
    headers = await make_headers(role="member")
    for i in range(3):
        await client.post(
            GROUPS_URL, headers=headers, json={"name": f"grp-all-{i}-{uuid.uuid4().hex[:6]}"}
        )

    full = await client.get(GROUPS_URL, headers=headers, params={"all": "true"})
    assert full.status_code == 200, full.text
    assert len(full.json()["items"]) == 3
    assert full.json()["total"] == 3


async def test_groups_pagination_keeps_members_expanded(client, make_headers):
    """分页不能把成员展开丢掉 —— GroupList 的 items 是 GroupWithMembers。"""
    headers = await make_headers(role="member")
    agent = (await _seed_agents(client, headers, 1))[0]
    group = await client.post(
        GROUPS_URL, headers=headers, json={"name": f"grp-mem-{uuid.uuid4().hex[:6]}"}
    )
    group_id = group.json()["id"]
    added = await client.post(
        f"{GROUPS_URL}/{group_id}/members",
        headers=headers,
        json={"agent_id": agent["id"], "position": 1},
    )
    assert added.status_code in (200, 201), added.text

    resp = await client.get(GROUPS_URL, headers=headers, params={"page": 1, "page_size": 10})
    item = resp.json()["items"][0]
    assert "members" in item
    assert len(item["members"]) == 1
    assert item["members"][0]["agent_id"] == agent["id"]


# ---------- /agents/{id}/egress-rules ----------


async def test_egress_rules_pagination_and_all_param(client, make_headers):
    """规则列表分页；?all=true 供编辑表单列全清单（否则靠后的规则删不掉）。"""
    headers = await make_headers(role="member")
    agent = (await _seed_agents(client, headers, 1))[0]
    rules_url = f"{AGENTS_URL}/{agent['id']}/egress-rules"

    for i in range(3):
        resp = await client.post(
            rules_url,
            headers=headers,
            json={"target_type": "domain", "target": f"host-{i}.example.test"},
        )
        assert resp.status_code == 201, resp.text

    paged = await client.get(rules_url, headers=headers, params={"page": 1, "page_size": 2})
    assert paged.status_code == 200, paged.text
    assert len(paged.json()["items"]) == 2
    # egress_mode 必须随分页一起返回（前端据此决定提示文案）
    assert "egress_mode" in paged.json()

    full = await client.get(rules_url, headers=headers, params={"all": "true"})
    assert full.status_code == 200, full.text
    assert len(full.json()["items"]) == 3


async def test_restricted_mode_warning_sees_rules_beyond_first_page(
    client, make_headers, db_session
):
    """反向保护：限制模式下判断「有无启用 allow 规则」必须看全集。

    ⚠️ 这条测试的构造要小心。Agent 级规则一律是 allow（服务层写死），
    且 enabled 不能通过 API 改，所以「被分页截断」的失效场景必须是：
      第 1 条 disabled、第 2 条 enabled
    若默认页长只看第 1 条，就会误判成「没有启用的 allow」并错误告警。
    早期版本我用 `action: deny` 构造第一条，但 EgressRuleIn 根本没有 action
    字段，该字段被静默忽略、两条都成了 allow，于是无论分页与否都通过 ——
    那条测试是假的。这里改用 enabled 构造，并反向验证过它会失败。
    """
    from sqlalchemy import select

    from app.models.egress import EgressRule

    headers = await make_headers(role="member")
    agent = (await _seed_agents(client, headers, 1))[0]
    rules_url = f"{AGENTS_URL}/{agent['id']}/egress-rules"

    # 第 1 条：disabled（落在第一页，不构成「启用中的 allow」）
    first = await client.post(
        rules_url,
        headers=headers,
        json={"target_type": "domain", "target": "disabled.example.test"},
    )
    assert first.status_code == 201, first.text
    # 第 2 条：enabled（关键的一条，要落在第一页之外）
    second = await client.post(
        rules_url,
        headers=headers,
        json={"target_type": "domain", "target": "enabled.example.test"},
    )
    assert second.status_code == 201, second.text

    # enabled 无法通过 API 关掉，直接改库
    rule = (
        await db_session.execute(
            select(EgressRule).where(EgressRule.id == first.json()["id"])
        )
    ).scalar_one()
    rule.enabled = False
    await db_session.commit()

    patched = await client.patch(
        f"{AGENTS_URL}/{agent['id']}",
        headers=headers,
        json={"egress_mode": "restricted"},
    )
    assert patched.status_code == 200, patched.text
    assert not patched.json().get("warning"), (
        "第 2 条规则是启用中的 allow，不该告警；出现告警说明内部判定被分页截断在第一页"
    )
