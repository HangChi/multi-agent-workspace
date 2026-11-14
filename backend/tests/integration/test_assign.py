"""分配集成测试（T051）：拖拽分配的 API 契约（US4 / FR-010~013）。

覆盖：
- 拖单 Agent 202（strategy 默认 direct）→ run_id + status=queued，Run 落库（assignee_type=agent）
- 拖组 fan_out 202 → 1 父 Run + N 个子 Run（子 Run 含 parent_run_id）
- 组策略省略 → 默认取组配置
- 需求已在执行 → 409（FR-012）
- 归档 Agent → 400
- 目标不存在 / 无权限 → 404
- 入队后需求 status=running（FR-013）
"""

import uuid

from sqlalchemy import select

from app.models.request import Request
from app.models.run import Run

REQUESTS_URL = "/api/v1/requests"
AGENTS_URL = "/api/v1/agents"
GROUPS_URL = "/api/v1/groups"


async def _create_agent(client, headers: dict, name: str = "agent") -> dict:
    resp = await client.post(
        AGENTS_URL,
        headers=headers,
        json={
            "name": name,
            "description": "a test agent",
            "system_prompt": "you are a helpful assistant",
            "model": "gpt-4o-mini",
        },
    )
    assert resp.status_code == 201
    return resp.json()


async def _create_group(client, headers: dict, strategy: str = "fan_out") -> dict:
    resp = await client.post(
        GROUPS_URL,
        headers=headers,
        json={"name": f"group-{uuid.uuid4().hex[:6]}", "routing_strategy": strategy},
    )
    assert resp.status_code == 201
    return resp.json()


async def _add_member(client, headers: dict, group_id: str, agent_id: str, position: int):
    resp = await client.post(
        f"{GROUPS_URL}/{group_id}/members",
        headers=headers,
        json={"agent_id": agent_id, "position": position},
    )
    assert resp.status_code == 201


async def _create_request(client, headers: dict, title: str = "assign-me") -> dict:
    resp = await client.post(
        REQUESTS_URL,
        headers=headers,
        json={"title": title, "description": "desc", "priority": "normal"},
    )
    assert resp.status_code == 201
    return resp.json()


async def _assign(client, headers: dict, request_id: str, payload: dict):
    return await client.post(
        f"{REQUESTS_URL}/{request_id}/assign", headers=headers, json=payload
    )


async def _runs_for(db_session, request_id: str) -> list[Run]:
    rows = (
        await db_session.execute(select(Run).where(Run.request_id == request_id))
    ).scalars().all()
    return list(rows)


# ---------- 拖单 Agent（direct）----------


async def test_assign_agent_direct_default(client, make_headers, db_session):
    """拖单 Agent 且省略 strategy → 202，默认 direct，需求变 running。"""
    headers = await make_headers(role="member")
    agent = await _create_agent(client, headers, name="direct-agent")
    req = await _create_request(client, headers)

    resp = await _assign(
        client,
        headers,
        req["id"],
        {"assignee_type": "agent", "assignee_id": agent["id"]},
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body["run_id"]
    assert body["status"] == "queued"

    runs = await _runs_for(db_session, req["id"])
    assert len(runs) == 1
    run = runs[0]
    assert run.id == body["run_id"]
    assert run.assignee_type == "agent"
    assert run.assignee_id == agent["id"]
    assert run.strategy == "direct"
    assert run.parent_run_id is None
    assert run.status == "queued"

    db_req = await db_session.get(Request, req["id"])
    assert db_req.status == "running"  # FR-013 入队后需求执行中


async def test_assign_agent_direct_explicit(client, make_headers, db_session):
    """显式 strategy=direct → 202。"""
    headers = await make_headers(role="member")
    agent = await _create_agent(client, headers)
    req = await _create_request(client, headers)

    resp = await _assign(
        client,
        headers,
        req["id"],
        {"assignee_type": "agent", "assignee_id": agent["id"], "strategy": "direct"},
    )
    assert resp.status_code == 202
    assert resp.json()["status"] == "queued"


# ---------- 拖组（fan_out）----------


async def test_assign_group_fan_out(client, make_headers, db_session):
    """拖组 fan_out → 202：1 父 Run + N 个子 Run（含 parent_run_id）。"""
    headers = await make_headers(role="member")
    group = await _create_group(client, headers, strategy="fan_out")
    agents = []
    for i in range(3):
        agent = await _create_agent(client, headers, name=f"member-{i}")
        agents.append(agent)
        await _add_member(client, headers, group["id"], agent["id"], i + 1)

    req = await _create_request(client, headers)
    resp = await _assign(
        client,
        headers,
        req["id"],
        {"assignee_type": "group", "assignee_id": group["id"], "strategy": "fan_out"},
    )
    assert resp.status_code == 202
    parent_run_id = resp.json()["run_id"]

    runs = await _runs_for(db_session, req["id"])
    # 1 父 Run + 3 子 Run
    assert len(runs) == 4

    parent = next(r for r in runs if r.parent_run_id is None)
    children = [r for r in runs if r.parent_run_id is not None]

    assert parent.id == parent_run_id
    assert parent.assignee_type == "group"
    assert parent.assignee_id == group["id"]
    assert parent.strategy == "fan_out"
    assert parent.status == "queued"

    assert len(children) == 3
    assert all(c.parent_run_id == parent.id for c in children)
    assert all(c.assignee_type == "agent" for c in children)
    # 展开出的成员与组配置一致（按 position 排序）
    member_ids = {a["id"] for a in agents}
    assert {c.assignee_id for c in children} == member_ids
    assert all(c.strategy == "fan_out" for c in children)

    db_req = await db_session.get(Request, req["id"])
    assert db_req.status == "running"


async def test_assign_group_default_strategy(client, make_headers, db_session):
    """拖组省略 strategy → 默认取组配置（fan_out）。"""
    headers = await make_headers(role="member")
    group = await _create_group(client, headers, strategy="fan_out")
    agent = await _create_agent(client, headers)
    await _add_member(client, headers, group["id"], agent["id"], 1)

    req = await _create_request(client, headers)
    resp = await _assign(
        client,
        headers,
        req["id"],
        {"assignee_type": "group", "assignee_id": group["id"]},
    )
    assert resp.status_code == 202

    runs = await _runs_for(db_session, req["id"])
    # 父 run 使用组配置 fan_out
    parent = next(r for r in runs if r.parent_run_id is None)
    assert parent.strategy == "fan_out"


# ---------- 幂等与状态冲突（FR-012）----------


async def test_assign_already_running_conflict(client, make_headers):
    """需求已在执行 → 再次分配 409。"""
    headers = await make_headers(role="member")
    agent = await _create_agent(client, headers)
    req = await _create_request(client, headers)

    first = await _assign(
        client,
        headers,
        req["id"],
        {"assignee_type": "agent", "assignee_id": agent["id"]},
    )
    assert first.status_code == 202

    second = await _assign(
        client,
        headers,
        req["id"],
        {"assignee_type": "agent", "assignee_id": agent["id"]},
    )
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "CONFLICT"


# ---------- 目标校验 ----------


async def test_assign_archived_agent(client, make_headers):
    """归档 Agent → 400 INVALID_INPUT。"""
    headers = await make_headers(role="member")
    agent = await _create_agent(client, headers)
    resp = await client.post(f"{AGENTS_URL}/{agent['id']}/archive", headers=headers)
    assert resp.status_code == 200

    req = await _create_request(client, headers)
    resp = await _assign(
        client,
        headers,
        req["id"],
        {"assignee_type": "agent", "assignee_id": agent["id"]},
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "INVALID_INPUT"


async def test_assign_target_not_found(client, make_headers):
    """目标不存在 → 404。"""
    headers = await make_headers(role="member")
    req = await _create_request(client, headers)
    resp = await _assign(
        client,
        headers,
        req["id"],
        {
            "assignee_type": "agent",
            "assignee_id": "00000000-0000-0000-0000-000000000000",
        },
    )
    assert resp.status_code == 404


async def test_assign_target_no_permission(client, auth_headers):
    """他人 Agent（无权限）→ 404（视为不存在，不泄露资源存在性）。"""
    owner_headers, _ = await auth_headers(role="member")
    agent = await _create_agent(client, owner_headers, name="private-agent")

    other_headers, _ = await auth_headers(role="member")
    req = await _create_request(client, other_headers)

    resp = await _assign(
        client,
        other_headers,
        req["id"],
        {"assignee_type": "agent", "assignee_id": agent["id"]},
    )
    assert resp.status_code == 404


async def test_assign_group_not_found(client, make_headers):
    """目标组不存在 → 404。"""
    headers = await make_headers(role="member")
    req = await _create_request(client, headers)
    resp = await _assign(
        client,
        headers,
        req["id"],
        {
            "assignee_type": "group",
            "assignee_id": "00000000-0000-0000-0000-000000000000",
        },
    )
    assert resp.status_code == 404


# ---------- 策略限制 ----------


async def test_assign_pipeline_not_implemented(client, make_headers):
    """pipeline 策略未实现（Phase 1 保留入口）→ 400。"""
    headers = await make_headers(role="member")
    agent = await _create_agent(client, headers)
    req = await _create_request(client, headers)
    resp = await _assign(
        client,
        headers,
        req["id"],
        {
            "assignee_type": "agent",
            "assignee_id": agent["id"],
            "strategy": "pipeline",
        },
    )
    assert resp.status_code == 400
    assert "未实现" in resp.json()["error"]["message"]


async def test_assign_fan_out_empty_group(client, make_headers):
    """组内无可执行 Agent → 400。"""
    headers = await make_headers(role="member")
    group = await _create_group(client, headers, strategy="fan_out")
    req = await _create_request(client, headers)
    resp = await _assign(
        client,
        headers,
        req["id"],
        {"assignee_type": "group", "assignee_id": group["id"]},
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "INVALID_INPUT"


async def test_assign_viewer_forbidden(client, auth_headers, make_headers):
    """viewer 角色 → 403（角色权限）。"""
    member_headers, _ = await auth_headers(role="member")
    req = await _create_request(client, member_headers)

    viewer_headers = await make_headers(role="viewer")
    resp = await _assign(
        client,
        viewer_headers,
        req["id"],
        {
            "assignee_type": "agent",
            "assignee_id": "00000000-0000-0000-0000-000000000000",
        },
    )
    assert resp.status_code == 403


# ---------- 受理性能（SC-002：拖拽受理 <3s）----------


async def test_assign_acceptance_latency(client, make_headers):
    """分配端点只做校验+建 Run+提交（不入队重活）→ 受理远低于 3s（SC-002）。"""
    import time

    headers = await make_headers(role="member")
    agent = await _create_agent(client, headers)
    req = await _create_request(client, headers)

    start = time.monotonic()
    resp = await _assign(
        client,
        headers,
        req["id"],
        {"assignee_type": "agent", "assignee_id": agent["id"]},
    )
    elapsed = time.monotonic() - start

    assert resp.status_code == 202
    assert elapsed < 3.0, f"分配受理耗时 {elapsed:.2f}s 超出 SC-002 阈值 3s"
