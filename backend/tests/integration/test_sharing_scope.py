"""010 共享可见性与协作谓词集成测试（T011，US2/US3/FR-004~007）。

本特性的回归面全在这里：扩的是「相关人员」集合，不是错误码。
既有测试里那些「陌生人 403」的用例全部仍是非成员——它们一条不断
（SC-004）；本文件钉新增的那一肢。
"""

import uuid

import pytest
from sqlalchemy import select

from app.models.project import Project
from app.models.request import Request
from app.models.user import User

pytestmark = pytest.mark.integration

REQUESTS_URL = "/api/v1/requests"
PROJECTS_URL = "/api/v1/projects"


async def _user(db, role: str = "member") -> User:
    user = User(
        email=f"scope-{uuid.uuid4().hex[:8]}@test.com",
        password_hash="x" * 60,
        role=role,
        status="active",
    )
    db.add(user)
    await db.commit()
    return user


async def _project(db, owner: User) -> Project:
    project = Project(owner_id=owner.id, name=f"共享项目-{uuid.uuid4().hex[:4]}")
    db.add(project)
    await db.commit()
    return project


async def _add_member(client, headers, project_id: str, user_id: str) -> None:
    resp = await client.post(
        f"{PROJECTS_URL}/{project_id}/members",
        json={"user_id": user_id},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text[:200]


# ---------- US2：可见性 ----------


async def test_member_sees_project_and_requests(db_session, client, make_headers):
    """FR-004/005：成员的 relevant 口径含共享项目与其全部需求（含 owner 建的）。"""
    owner = await _user(db_session)
    member = await _user(db_session)
    project = await _project(db_session, owner)

    # owner 名下 2 条归属该项目的需求 + 1 条无归属
    for i in range(2):
        db_session.add(
            Request(
                owner_id=owner.id,
                title=f"项目内{i}",
                project_id=project.id,
                project_name=project.name,
            )
        )
    db_session.add(Request(owner_id=owner.id, title="owner 的私活"))
    await db_session.commit()

    member_headers = await make_headers(member)
    # 加人前：member 什么都看不到
    before = await client.get(REQUESTS_URL, headers=member_headers)
    assert all("项目内" not in r["title"] for r in before.json()["items"])

    await _add_member(client, await make_headers(owner), project.id, member.id)

    # 加人后：项目与项目内需求立即可见（同会话，不等缓存）
    projects = await client.get(
        f"{PROJECTS_URL}?scope=relevant", headers=member_headers
    )
    assert project.id in {p["id"] for p in projects.json()["items"]}
    requests = await client.get(REQUESTS_URL, headers=member_headers)
    titles = {r["title"] for r in requests.json()["items"]}
    assert {"项目内0", "项目内1"} <= titles
    assert "owner 的私活" not in titles  # 非项目需求仍不可见

    # 详情 200（此前非相关人员 403）
    target = (
        (await db_session.execute(select(Request).where(Request.title == "项目内0")))
        .scalars()
        .one()
    )
    detail = await client.get(f"{REQUESTS_URL}/{target.id}", headers=member_headers)
    assert detail.status_code == 200


async def test_visibility_revoked_immediately(db_session, client, make_headers):
    """FR-008/SC-003：移除成员后，其 token 再查即 404/不可见（立即收紧）。"""
    owner = await _user(db_session)
    member = await _user(db_session)
    project = await _project(db_session, owner)
    await _add_member(client, await make_headers(owner), project.id, member.id)
    req = Request(
        owner_id=owner.id,
        title="收紧用例",
        project_id=project.id,
        project_name=project.name,
    )
    db_session.add(req)
    await db_session.commit()

    member_headers = await make_headers(member)
    ok = await client.get(f"{REQUESTS_URL}/{req.id}", headers=member_headers)
    assert ok.status_code == 200

    resp = await client.delete(
        f"{PROJECTS_URL}/{project.id}/members/{member.id}",
        headers=await make_headers(owner),
    )
    assert resp.status_code == 204

    gone = await client.get(f"{REQUESTS_URL}/{req.id}", headers=member_headers)
    assert gone.status_code == 403  # 非相关人员维持 007 错误码
    listing = await client.get(REQUESTS_URL, headers=member_headers)
    assert all(r["id"] != req.id for r in listing.json()["items"])


# ---------- US3：协作链 ----------


async def test_member_collaboration_chain(db_session, client, make_headers):
    """SC-002/FR-006：成员 建→改→分配（门禁照查）→连边 全环打通。"""
    from app.models.agent import Agent

    owner = await _user(db_session)
    member = await _user(db_session)
    project = await _project(db_session, owner)
    await _add_member(client, await make_headers(owner), project.id, member.id)

    agent = Agent(
        owner_id=owner.id,
        name=f"协作验收 {uuid.uuid4().hex[:4]}",
        system_prompt="x",
        model="gpt-4o-mini",
        max_steps=4,
        cost_limit_cents=10000,
        tools=[],
        status="active",
    )
    db_session.add(agent)
    await db_session.commit()
    member_headers = await make_headers(member)

    # ① 建需求进共享项目（FR-007：active 且我是成员）
    created = await client.post(
        REQUESTS_URL,
        json={"title": "成员建的", "project_id": project.id},
        headers=member_headers,
    )
    assert created.status_code == 201

    # ② 编辑 owner 建的项目内需求
    owner_req = Request(
        owner_id=owner.id,
        title="owner 建的",
        project_id=project.id,
        project_name=project.name,
    )
    db_session.add(owner_req)
    await db_session.commit()
    edited = await client.patch(
        f"{REQUESTS_URL}/{owner_req.id}",
        json={"title": "被成员改过的"},
        headers=member_headers,
    )
    assert edited.status_code == 200

    # ③ 分配执行（202；009 门禁照查——无前置直接放行）
    assigned = await client.post(
        f"{REQUESTS_URL}/{owner_req.id}/assign",
        json={"assignee_type": "agent", "assignee_id": agent.id},
        headers=member_headers,
    )
    assert assigned.status_code == 202

    # ④ 连依赖边（009 边端点谓词同样扩展）
    edge = await client.post(
        f"{REQUESTS_URL}/{created.json()['id']}/dependencies",
        json={"dependency_id": owner_req.id},
        headers=member_headers,
    )
    assert edge.status_code == 201


async def test_member_cannot_change_project_ownership(db_session, client, make_headers):
    """R-03：成员改需求 project_id → 403（仅创建者或该项目 owner）。"""
    owner = await _user(db_session)
    member = await _user(db_session)
    project = await _project(db_session, owner)
    other = await _project(db_session, owner)
    await _add_member(client, await make_headers(owner), project.id, member.id)
    req = Request(
        owner_id=member.id,
        title="成员建的",
        project_id=project.id,
        project_name=project.name,
    )
    db_session.add(req)
    await db_session.commit()

    # 成员即便是创建者本人也不行——规则是「创建者或该项目 owner」，
    # 成员只是成员（项目 owner 才是结构负责人）。等一下：创建者本人
    # 就是 owner_id==member，按规则应该可以。这条用例改用「另一成员」。
    member2 = await _user(db_session)
    await _add_member(client, await make_headers(owner), project.id, member2.id)
    blocked = await client.patch(
        f"{REQUESTS_URL}/{req.id}",
        json={"project_id": other.id},
        headers=await make_headers(member2),
    )
    assert blocked.status_code == 403

    # 创建者本人可以改自己的需求归属——但目标项目必须是他**参与**的
    # （移到没参与的项目会 404：否则等于把活塞进一个自己看不见的地方）。
    # 先让成员建自己的项目，再挪过去。
    own_project = (
        await client.post(
            "/api/v1/projects",
            json={"name": f"成员自己的-{uuid.uuid4().hex[:4]}"},
            headers=await make_headers(member),
        )
    ).json()
    allowed = await client.patch(
        f"{REQUESTS_URL}/{req.id}",
        json={"project_id": own_project["id"]},
        headers=await make_headers(member),
    )
    assert allowed.status_code == 200


async def test_project_owner_can_move_any_request(db_session, client, make_headers):
    """R-03 的另一半：项目 owner 可改项目内任何需求的归属（含成员的）。"""
    owner = await _user(db_session)
    member = await _user(db_session)
    project = await _project(db_session, owner)
    other = await _project(db_session, owner)
    await _add_member(client, await make_headers(owner), project.id, member.id)
    req = Request(
        owner_id=member.id,
        title="成员的",
        project_id=project.id,
        project_name=project.name,
    )
    db_session.add(req)
    await db_session.commit()

    moved = await client.patch(
        f"{REQUESTS_URL}/{req.id}",
        json={"project_id": other.id},
        headers=await make_headers(owner),
    )
    assert moved.status_code == 200


async def test_accept_still_assignee_only(db_session, client, make_headers):
    """R-07：共享不扩展 accept——责任接收仍是个人动作（007 锁定）。"""
    owner = await _user(db_session)
    member = await _user(db_session)
    worker = await _user(db_session)
    project = await _project(db_session, owner)
    await _add_member(client, await make_headers(owner), project.id, member.id)
    req = Request(
        owner_id=owner.id,
        title="待接收",
        project_id=project.id,
        project_name=project.name,
        status="pending",
        assignee_id=worker.id,
    )
    db_session.add(req)
    await db_session.commit()

    # 成员（非 assignee）不能替 worker 接收。404 而非 403：accept 是 007
    # 锁定契约的分派端点，对「非当前接收人」一律 404 不暴露存在性
    # （dispatch_service._check_access）——共享不改这个码。
    blocked = await client.post(
        f"{REQUESTS_URL}/{req.id}/accept", headers=await make_headers(member)
    )
    assert blocked.status_code == 404
    # worker 本人可以（accept 成功是 202 Accepted）
    ok = await client.post(
        f"{REQUESTS_URL}/{req.id}/accept", headers=await make_headers(worker)
    )
    assert ok.status_code == 202


# ---------- 陌生人维持（SC-004 的新增面） ----------


async def test_stranger_still_isolated(db_session, client, make_headers):
    """陌生人（非 owner 非成员）对共享项目与其需求维持 404/403。"""
    owner = await _user(db_session)
    member = await _user(db_session)
    stranger = await _user(db_session)
    project = await _project(db_session, owner)
    await _add_member(client, await make_headers(owner), project.id, member.id)
    req = Request(
        owner_id=owner.id,
        title="隔离用例",
        project_id=project.id,
        project_name=project.name,
    )
    db_session.add(req)
    await db_session.commit()

    stranger_headers = await make_headers(stranger)
    detail = await client.get(f"{REQUESTS_URL}/{req.id}", headers=stranger_headers)
    assert detail.status_code == 403
    project_view = await client.get(
        f"{PROJECTS_URL}/{project.id}", headers=stranger_headers
    )
    assert project_view.status_code == 404
    listing = await client.get(REQUESTS_URL, headers=stranger_headers)
    assert all(r["id"] != req.id for r in listing.json()["items"])


async def test_scope_mine_unchanged(db_session, client, make_headers):
    """scope=mine 不变：成员的 mine 列表仍只有自己创建的（不含共享项目需求）。"""
    owner = await _user(db_session)
    member = await _user(db_session)
    project = await _project(db_session, owner)
    await _add_member(client, await make_headers(owner), project.id, member.id)
    req = Request(
        owner_id=owner.id,
        title="共享里的",
        project_id=project.id,
        project_name=project.name,
    )
    db_session.add(req)
    await db_session.commit()

    mine = await client.get(
        f"{REQUESTS_URL}?scope=mine", headers=await make_headers(member)
    )
    assert all(r["id"] != req.id for r in mine.json()["items"])
