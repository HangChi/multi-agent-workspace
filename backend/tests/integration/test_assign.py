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

from app.models.external_tool import (
    ExternalToolGrant,
    ExternalToolServer,
    ExternalToolVersion,
    MarketplaceListing,
    RunExternalToolSnapshot,
    ToolInstallation,
)
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


async def test_concurrent_assign_only_one_wins(client, make_headers, db_session):
    """并发分配同一需求：行级锁保证只有一个受理，其余 409（不产生重复 Run）。

    无 SELECT ... FOR UPDATE 时，两个请求会同时读到 status=created，
    各自建一组 Run → 同一需求被执行两次、成本翻倍。
    """
    import asyncio

    headers = await make_headers(role="member")
    agent = await _create_agent(client, headers, name="concurrent-agent")
    req = await _create_request(client, headers, title="concurrent-assign")

    payload = {"assignee_type": "agent", "assignee_id": agent["id"]}
    responses = await asyncio.gather(
        *[_assign(client, headers, req["id"], payload) for _ in range(4)]
    )
    codes = sorted(r.status_code for r in responses)

    assert codes.count(202) == 1, f"应只有 1 个受理，实际 {codes}"
    assert codes.count(409) == 3, f"其余应为 409，实际 {codes}"

    # 只落 1 个 Run，需求只被消耗一次
    runs = await _runs_for(db_session, req["id"])
    assert len(runs) == 1


async def test_assign_group_pipeline_creates_ordered_chain(
    client, make_headers, db_session, monkeypatch
):
    """pipeline → 父 Run + 按 position 排序的子 Run（sequence=1..N），只入队第一阶段。"""
    from app.services import assignment_service

    enqueued: list[str] = []
    monkeypatch.setattr(assignment_service, "_enqueue", lambda ids: enqueued.extend(ids))

    headers = await make_headers(role="member")
    first = await _create_agent(client, headers, name="写手")
    second = await _create_agent(client, headers, name="编辑")
    group = await _create_group(client, headers, strategy="pipeline")
    await _add_member(client, headers, group["id"], first["id"], position=1)
    await _add_member(client, headers, group["id"], second["id"], position=2)
    req = await _create_request(client, headers, title="pipeline-req")

    resp = await _assign(
        client, headers, req["id"], {"assignee_type": "group", "assignee_id": group["id"]}
    )
    assert resp.status_code == 202

    runs = await _runs_for(db_session, req["id"])
    parent = next(r for r in runs if r.parent_run_id is None)
    children = sorted([r for r in runs if r.parent_run_id is not None], key=lambda r: r.sequence)

    assert parent.assignee_type == "group"
    assert parent.strategy == "pipeline"
    assert [c.sequence for c in children] == [1, 2]
    assert [c.assignee_id for c in children] == [first["id"], second["id"]]
    assert all(c.status == "queued" for c in children)

    # 只入队第一阶段；父 Run 不入队（由最后一阶段收敛）
    assert enqueued == [children[0].id]


async def test_assign_group_router_creates_single_run(client, make_headers, db_session):
    """router → 只建一个父 Run（运行时选成员），不建子 Run。"""
    headers = await make_headers(role="member")
    a1 = await _create_agent(client, headers, name="通用助手")
    group = await _create_group(client, headers, strategy="router")
    await _add_member(client, headers, group["id"], a1["id"], position=1)
    req = await _create_request(client, headers, title="router-req")

    resp = await _assign(
        client, headers, req["id"], {"assignee_type": "group", "assignee_id": group["id"]}
    )
    assert resp.status_code == 202

    runs = await _runs_for(db_session, req["id"])
    assert len(runs) == 1
    assert runs[0].assignee_type == "group"
    assert runs[0].strategy == "router"
    assert runs[0].parent_run_id is None


async def test_assign_group_strategy_with_agent_target_rejected(client, make_headers):
    """组类策略拖到单个 Agent → 400（语义错误，早失败）。"""
    headers = await make_headers(role="member")
    agent = await _create_agent(client, headers, name="单兵")
    req = await _create_request(client, headers, title="bad-target")

    resp = await _assign(
        client,
        headers,
        req["id"],
        {"assignee_type": "agent", "assignee_id": agent["id"], "strategy": "pipeline"},
    )
    assert resp.status_code == 400


async def test_assign_freezes_external_grant_version_and_schema_snapshot(client, auth_headers, db_session):
    """Assignment captures external identity/schema independently of later state changes."""
    headers, owner = await auth_headers(role="member")
    agent = await _create_agent(client, headers, name="external-snapshot-agent")
    request = await _create_request(client, headers, title="external-snapshot")
    server = ExternalToolServer(
        owner_id=owner.id,
        created_by=owner.id,
        name="Snapshot Server",
        description="snapshot fixture",
        endpoint_url="https://8.8.8.8/snapshot",
        normalized_endpoint_url="https://8.8.8.8/snapshot",
        maintainer="Platform",
        auth_mode="none",
        status="published",
        current_validation_status="passed",
    )
    db_session.add(server)
    await db_session.flush()
    version = ExternalToolVersion(
        server_id=server.id,
        external_name="lookup",
        stable_name="mcp_snapshot_lookup_v1",
        version="1.2.3+snapshot",
        description="Lookup",
        input_schema={"type": "object", "properties": {"q": {"type": "string"}}},
        required_permissions=["records.read"],
        definition_hash="snapshot-definition",
        availability="valid",
    )
    db_session.add(version)
    await db_session.flush()
    listing = MarketplaceListing(
        tool_version_id=version.id,
        status="published",
        usage_summary="Lookup",
        data_handling="Sends query to server",
        restrictions={},
    )
    db_session.add(listing)
    await db_session.flush()
    installation = ToolInstallation(
        listing_id=listing.id,
        owner_id=owner.id,
        installed_by=owner.id,
        status="installed",
        version_policy="pinned",
    )
    db_session.add(installation)
    await db_session.flush()
    grant = ExternalToolGrant(
        agent_id=agent["id"],
        installation_id=installation.id,
        tool_version_id=version.id,
        permission_snapshot=["records.read"],
        schema_snapshot={"input": version.input_schema, "output": None},
        status="active",
        granted_by=owner.id,
    )
    db_session.add(grant)
    await db_session.commit()

    response = await _assign(
        client,
        headers,
        request["id"],
        {"assignee_type": "agent", "assignee_id": agent["id"]},
    )
    assert response.status_code == 202, response.text
    snapshot = await db_session.scalar(
        select(RunExternalToolSnapshot).where(RunExternalToolSnapshot.run_id == response.json()["run_id"])
    )
    assert snapshot is not None
    assert snapshot.grant_id == grant.id
    assert snapshot.tool_version_id == version.id
    assert snapshot.version == "1.2.3+snapshot"
    assert snapshot.schema_snapshot == {"input": version.input_schema, "output": None}
