"""021 依赖图谱与自动启动：算法单测 + 端点/权限集成测试。

算法部分不碰 DB（纯函数），端点部分验权限与形状。自动启动的**真实执行**
需要 worker，放在 `deploy/verify/s17_dependency_flow.py` 里验收。
"""

import uuid

import pytest

from app.models.project import Project
from app.models.request import Request
from app.models.request_dependency import RequestDependency
from app.models.user import User
from app.services.dependency_graph_service import _critical_path, _topological_order

pytestmark = pytest.mark.integration

REQUESTS_URL = "/api/v1/requests"


# ---------------------------------------------------------------- 纯算法


def test_topological_order_chain() -> None:
    nodes = ["a", "b", "c"]
    edges = [("b", "a"), ("c", "b")]  # b 依赖 a，c 依赖 b
    order, has_cycle = _topological_order(nodes, edges)
    assert order == ["a", "b", "c"]
    assert has_cycle is False


def test_topological_order_detects_cycle() -> None:
    nodes = ["a", "b"]
    edges = [("a", "b"), ("b", "a")]
    order, has_cycle = _topological_order(nodes, edges)
    assert has_cycle is True
    assert order == []


def test_critical_path_picks_longest_chain() -> None:
    """a→b→d 比 a→c 长，关键路径应选前者。"""
    nodes = ["a", "b", "c", "d"]
    edges = [("b", "a"), ("c", "a"), ("d", "b")]
    assert _critical_path(nodes, edges) == ["a", "b", "d"]


def test_critical_path_empty_without_edges() -> None:
    assert _critical_path(["a"], []) == []


# ---------------------------------------------------------------- 端点


async def _user(db) -> User:
    user = User(
        email=f"graph-{uuid.uuid4().hex[:8]}@test.com",
        password_hash="x" * 60,
        role="member",
        status="active",
    )
    db.add(user)
    await db.commit()
    return user


async def _request(db, owner: User, title: str) -> Request:
    req = Request(title=title, owner_id=owner.id)
    db.add(req)
    await db.commit()
    return req


async def _edge(db, dependent: Request, dependency: Request) -> None:
    db.add(
        RequestDependency(
            request_id=dependent.id,
            dependency_id=dependency.id,
            created_by=dependent.owner_id,
        )
    )
    await db.commit()


async def test_graph_returns_chain(db_session, client, make_headers) -> None:
    owner = await _user(db_session)
    a = await _request(db_session, owner, "A 前置")
    b = await _request(db_session, owner, "B 中间")
    c = await _request(db_session, owner, "C 收尾")
    await _edge(db_session, b, a)
    await _edge(db_session, c, b)

    resp = await client.get(
        f"{REQUESTS_URL}/{a.id}/dependency-graph", headers=await make_headers(owner)
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [n["id"] for n in body["nodes"]] == [a.id, b.id, c.id]
    assert body["topo_order"] == [a.id, b.id, c.id]
    assert body["critical_path"] == [a.id, b.id, c.id]
    assert body["has_cycle"] is False
    # b、c 都还有未完成前置
    unmet = {n["id"]: n["unmet_count"] for n in body["nodes"]}
    assert unmet[b.id] == 1 and unmet[c.id] == 1 and unmet[a.id] == 0


async def test_graph_requires_visibility(db_session, client, make_headers) -> None:
    owner = await _user(db_session)
    stranger = await _user(db_session)
    a = await _request(db_session, owner, "别人的需求")

    resp = await client.get(
        f"{REQUESTS_URL}/{a.id}/dependency-graph",
        headers=await make_headers(stranger),
    )
    assert resp.status_code == 404


async def test_project_member_can_read_graph(
    db_session, client, make_headers
) -> None:
    owner = await _user(db_session)
    member = await _user(db_session)
    project = Project(owner_id=owner.id, name=f"图谱项目-{uuid.uuid4().hex[:5]}")
    db_session.add(project)
    await db_session.commit()
    req = Request(title="项目内需求", owner_id=owner.id, project_id=project.id)
    db_session.add(req)
    await db_session.commit()
    from app.models.project_member import ProjectMember

    db_session.add(ProjectMember(project_id=project.id, user_id=member.id))
    await db_session.commit()

    resp = await client.get(
        f"{REQUESTS_URL}/{req.id}/dependency-graph", headers=await make_headers(member)
    )
    assert resp.status_code == 200
    assert resp.json()["root_id"] == req.id


# ---------------------------------------------------------------- 自动启动字段


async def test_owner_can_set_auto_start(db_session, client, make_headers) -> None:
    owner = await _user(db_session)
    req = await _request(db_session, owner, "自动启动需求")
    agent = await client.post(
        "/api/v1/agents",
        json={
            "name": f"auto-{uuid.uuid4().hex[:5]}",
            "system_prompt": "x",
            "model": "Deepseek-V4.1-Flash",
            "max_steps": 2,
            "cost_limit_cents": 1000,
            "tools": [],
        },
        headers=await make_headers(owner),
    )
    assert agent.status_code == 201, agent.text[:200]

    resp = await client.patch(
        f"{REQUESTS_URL}/{req.id}",
        json={"auto_start": True, "auto_agent_id": agent.json()["id"]},
        headers=await make_headers(owner),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["auto_start"] is True
    assert resp.json()["auto_agent_id"] == agent.json()["id"]


async def test_member_cannot_set_auto_start(db_session, client, make_headers) -> None:
    """自动启动会真的花钱：权限与「改项目归属」同档（创建者或项目 owner）。"""
    owner = await _user(db_session)
    member = await _user(db_session)
    project = Project(owner_id=owner.id, name=f"权限项目-{uuid.uuid4().hex[:5]}")
    db_session.add(project)
    await db_session.commit()
    req = Request(title="成员想开自动", owner_id=owner.id, project_id=project.id)
    db_session.add(req)
    await db_session.commit()
    from app.models.project_member import ProjectMember

    db_session.add(ProjectMember(project_id=project.id, user_id=member.id))
    await db_session.commit()

    resp = await client.patch(
        f"{REQUESTS_URL}/{req.id}",
        json={"auto_start": True},
        headers=await make_headers(member),
    )
    assert resp.status_code == 403
    assert "自动启动" in resp.json()["error"]["message"]


async def test_unknown_agent_rejected(db_session, client, make_headers) -> None:
    owner = await _user(db_session)
    req = await _request(db_session, owner, "不存在的 agent")

    resp = await client.patch(
        f"{REQUESTS_URL}/{req.id}",
        json={"auto_start": True, "auto_agent_id": str(uuid.uuid4())},
        headers=await make_headers(owner),
    )
    assert resp.status_code == 404
