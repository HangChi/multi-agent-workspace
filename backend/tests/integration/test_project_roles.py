"""019 项目成员角色（editor / viewer）的集成测试。

三条边界要钉住：
1. **只读成员看得到**（项目、需求、成员列表）——这正是给他 role 的目的，
   如果他连看都看不到，那还不如不加成员；
2. **只读成员改不了**（PATCH 需求、分配执行）→ 403 而不是 404：
   他知道这些东西存在，只是不被允许动（016 的分工）；
3. **默认角色是 editor**：019 之前的既有成员行迁移后行为不变（零回填）。
"""

import uuid

import pytest
from sqlalchemy import select

from app.api.errors import AppError
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.request import Request
from app.models.user import User
from app.services import project_member_service

pytestmark = pytest.mark.integration

PROJECTS_URL = "/api/v1/projects"
REQUESTS_URL = "/api/v1/requests"
AGENTS_URL = "/api/v1/agents"


async def _user(db, role: str = "member") -> User:
    user = User(
        email=f"role-{uuid.uuid4().hex[:8]}@test.com",
        password_hash="x" * 60,
        role=role,
        status="active",
    )
    db.add(user)
    await db.commit()
    return user


async def _project(db, owner: User) -> Project:
    project = Project(owner_id=owner.id, name=f"角色项目-{uuid.uuid4().hex[:6]}")
    db.add(project)
    await db.commit()
    return project


async def _request(db, owner: User, project: Project) -> Request:
    request = Request(
        title=f"角色需求-{uuid.uuid4().hex[:6]}",
        owner_id=owner.id,
        project_id=project.id,
        project_name=project.name,
    )
    db.add(request)
    await db.commit()
    return request


async def _member(db, project: Project, user: User, role: str = "editor") -> ProjectMember:
    edge = ProjectMember(project_id=project.id, user_id=user.id, role=role)
    db.add(edge)
    await db.commit()
    return edge


# ---------------------------------------------------------------- 角色落库与读取


async def test_owner_row_reports_owner_role(db_session, client, make_headers) -> None:
    owner = await _user(db_session)
    project = await _project(db_session, owner)

    resp = await client.get(
        f"{PROJECTS_URL}/{project.id}/members", headers=await make_headers(owner)
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["is_owner"] is True
    # owner 不在成员表里（R-01），列表合成时角色标 owner
    assert items[0]["role"] == "owner"


async def test_default_member_role_is_editor(db_session) -> None:
    """019 之前的既有成员行：迁移后默认 editor，行为不变（零回填）。"""
    owner = await _user(db_session)
    colleague = await _user(db_session)
    project = await _project(db_session, owner)
    edge = ProjectMember(project_id=project.id, user_id=colleague.id)
    db_session.add(edge)
    await db_session.commit()

    stored = (
        await db_session.execute(
            select(ProjectMember.role).where(
                ProjectMember.project_id == project.id,
                ProjectMember.user_id == colleague.id,
            )
        )
    ).scalar_one()
    assert stored == "editor"
    assert await project_member_service.can_write_in_project(
        db_session, project.id, colleague.id
    )


async def test_add_viewer_via_api(db_session, client, make_headers) -> None:
    owner = await _user(db_session)
    viewer = await _user(db_session)
    project = await _project(db_session, owner)

    resp = await client.post(
        f"{PROJECTS_URL}/{project.id}/members",
        json={"user_id": viewer.id, "role": "viewer"},
        headers=await make_headers(owner),
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["role"] == "viewer"

    listed = await client.get(
        f"{PROJECTS_URL}/{project.id}/members", headers=await make_headers(owner)
    )
    roles = {m["user_id"]: m["role"] for m in listed.json()["items"]}
    assert roles[viewer.id] == "viewer"
    assert roles[owner.id] == "owner"


async def test_invalid_role_rejected(db_session, client, make_headers) -> None:
    owner = await _user(db_session)
    other = await _user(db_session)
    project = await _project(db_session, owner)

    # HTTP 层：Literal 校验 → 422
    resp = await client.post(
        f"{PROJECTS_URL}/{project.id}/members",
        json={"user_id": other.id, "role": "admin"},
        headers=await make_headers(owner),
    )
    assert resp.status_code == 422

    # service 层直接调用：不该让非法角色落库（脚本/内部调用也走这条路）
    with pytest.raises(AppError) as exc:
        await project_member_service.add_member(
            db_session,
            project_id=project.id,
            user_id=owner.id,
            member_user_id=other.id,
            role="superuser",
        )
    assert exc.value.status_code == 400


# ---------------------------------------------------------------- 只读成员：能看


async def test_viewer_can_read_project_and_request(
    db_session, client, make_headers
) -> None:
    owner = await _user(db_session)
    viewer = await _user(db_session)
    project = await _project(db_session, owner)
    request = await _request(db_session, owner, project)
    await _member(db_session, project, viewer, "viewer")
    headers = await make_headers(viewer)

    assert (await client.get(f"{PROJECTS_URL}/{project.id}", headers=headers)).status_code == 200
    detail = await client.get(f"{REQUESTS_URL}/{request.id}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["title"] == request.title

    listed = await client.get(f"{REQUESTS_URL}?project_id={project.id}", headers=headers)
    assert listed.status_code == 200
    assert any(r["id"] == request.id for r in listed.json()["items"])


# ---------------------------------------------------------------- 只读成员：改不了


async def test_viewer_cannot_patch_request(db_session, client, make_headers) -> None:
    owner = await _user(db_session)
    viewer = await _user(db_session)
    project = await _project(db_session, owner)
    request = await _request(db_session, owner, project)
    await _member(db_session, project, viewer, "viewer")

    resp = await client.patch(
        f"{REQUESTS_URL}/{request.id}",
        json={"title": "只读成员改的标题"},
        headers=await make_headers(viewer),
    )
    assert resp.status_code == 403
    assert "只读成员" in resp.json()["error"]["message"]

    await db_session.refresh(request)
    assert request.title != "只读成员改的标题"


async def test_viewer_cannot_assign(db_session, client, make_headers) -> None:
    owner = await _user(db_session)
    viewer = await _user(db_session)
    project = await _project(db_session, owner)
    request = await _request(db_session, owner, project)
    await _member(db_session, project, viewer, "viewer")

    agent = await client.post(
        AGENTS_URL,
        json={
            "name": f"角色 agent-{uuid.uuid4().hex[:5]}",
            "system_prompt": "x",
            "model": "Deepseek-V4.1-Flash",
            "max_steps": 2,
            "cost_limit_cents": 1000,
            "tools": [],
        },
        headers=await make_headers(owner),
    )
    assert agent.status_code == 201, agent.text[:200]

    resp = await client.post(
        f"{REQUESTS_URL}/{request.id}/assign",
        json={"assignee_type": "agent", "assignee_id": agent.json()["id"]},
        headers=await make_headers(viewer),
    )
    assert resp.status_code == 403
    assert "只读成员" in resp.json()["error"]["message"]


async def test_editor_can_patch_request(db_session, client, make_headers) -> None:
    """默认 editor 的既有行为不能被 019 弄坏（回归）。"""
    owner = await _user(db_session)
    editor = await _user(db_session)
    project = await _project(db_session, owner)
    request = await _request(db_session, owner, project)
    await _member(db_session, project, editor, "editor")

    resp = await client.patch(
        f"{REQUESTS_URL}/{request.id}",
        json={"title": "编辑成员改的标题"},
        headers=await make_headers(editor),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["title"] == "编辑成员改的标题"


async def test_owner_is_not_blocked_by_role_check(
    db_session, client, make_headers
) -> None:
    """owner 不在成员表里（R-01）——写权限判定必须单独认 owner_id。"""
    owner = await _user(db_session)
    project = await _project(db_session, owner)
    request = await _request(db_session, owner, project)

    assert await project_member_service.member_role(
        db_session, project.id, owner.id
    ) == "owner"
    assert await project_member_service.can_write_in_project(
        db_session, project.id, owner.id
    )

    resp = await client.patch(
        f"{REQUESTS_URL}/{request.id}",
        json={"title": "owner 改的标题"},
        headers=await make_headers(owner),
    )
    assert resp.status_code == 200


async def test_non_participant_still_404(db_session, client, make_headers) -> None:
    """陌生人不在本次改动范围内：仍然 404（不暴露存在性）。"""
    owner = await _user(db_session)
    stranger = await _user(db_session)
    project = await _project(db_session, owner)
    request = await _request(db_session, owner, project)

    resp = await client.patch(
        f"{REQUESTS_URL}/{request.id}",
        json={"title": "x"},
        headers=await make_headers(stranger),
    )
    assert resp.status_code == 404
