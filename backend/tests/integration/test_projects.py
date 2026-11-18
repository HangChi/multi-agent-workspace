"""008 项目端点集成测试（T007，P-1~P-4/P-8/P-9）：CRUD + 归属 + RBAC + 计数。

setup 全部 commit（API 走独立 session，看不到未提交数据——项目既有约定）。
契约判定顺序见 contracts/api.md §1：认证 → 角色 403 → schema 422 → 同名 409
→ 落库审计 201。
"""

import uuid

import pytest
from sqlalchemy import func, select

from app.models.audit_log import AuditLog
from app.models.project import Project
from app.models.request import Request
from app.models.user import User

pytestmark = pytest.mark.integration

PROJECTS_URL = "/api/v1/projects"


async def _user(db, role: str = "member") -> User:
    user = User(
        email=f"proj-api-{uuid.uuid4().hex[:8]}@test.com",
        password_hash="x" * 60,
        role=role,
        status="active",
    )
    db.add(user)
    await db.commit()
    return user


async def _make_project(db, owner: User, name: str | None = None, **kw) -> Project:
    project = Project(
        owner_id=owner.id, name=name or f"项目-{uuid.uuid4().hex[:6]}", **kw
    )
    db.add(project)
    await db.commit()
    return project


# ---------- P-1：建项目 ----------


async def test_create_project_201_and_first_in_list(db_session, client, make_headers):
    """P-1：建项目 201 + 出现在列表首位（创建时间倒序）。"""
    owner = await _user(db_session)
    resp = await client.post(
        PROJECTS_URL,
        json={"name": "官网改版", "description": "Q4 官网重构"},
        headers=await make_headers(owner),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "官网改版"
    assert body["status"] == "active"
    assert body["request_count"] == 0

    listing = await client.get(PROJECTS_URL, headers=await make_headers(owner))
    assert listing.status_code == 200
    assert listing.json()["items"][0]["name"] == "官网改版"


# ---------- P-2：同名 409 ----------


async def test_duplicate_name_409(db_session, client, make_headers):
    """P-2：同用户名下同名 → 409（含已归档项目也占名字）。"""
    owner = await _user(db_session)
    await _make_project(db_session, owner, name="占用名")
    resp = await client.post(
        PROJECTS_URL, json={"name": "占用名"}, headers=await make_headers(owner)
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "CONFLICT"


async def test_archived_project_still_holds_name(db_session, client, make_headers):
    """P-2 延伸：归档项目仍占名字——防「归档后建同名」搅浑历史归属。"""
    owner = await _user(db_session)
    project = await _make_project(db_session, owner, name="旧项目名")
    project.status = "archived"
    db_session.add(project)
    await db_session.commit()

    resp = await client.post(
        PROJECTS_URL, json={"name": "旧项目名"}, headers=await make_headers(owner)
    )
    assert resp.status_code == 409


# ---------- P-3：他人项目 404 ----------


async def test_others_project_404(db_session, client, make_headers):
    """P-3：读/改/删他人项目一律 404（不暴露存在性）。"""
    owner = await _user(db_session)
    stranger = await _user(db_session)
    project = await _make_project(db_session, owner)

    for method, url in (
        ("GET", f"{PROJECTS_URL}/{project.id}"),
        ("PATCH", f"{PROJECTS_URL}/{project.id}"),
        ("DELETE", f"{PROJECTS_URL}/{project.id}"),
    ):
        resp = await client.request(
            method,
            url,
            headers=await make_headers(stranger),
            json={"name": "改个名"} if method == "PATCH" else None,
        )
        assert resp.status_code == 404, f"{method} 应 404，实得 {resp.status_code}"


# ---------- P-4：viewer 403 ----------


async def test_viewer_forbidden(db_session, client, make_headers):
    """P-4：viewer 对所有项目端点 403（与 agents/groups 的 RBAC 一致）。"""
    viewer = await _user(db_session, role="viewer")
    headers = await make_headers(viewer)

    resp = await client.post(PROJECTS_URL, json={"name": "viewer 的"}, headers=headers)
    assert resp.status_code == 403
    listing = await client.get(PROJECTS_URL, headers=headers)
    assert listing.status_code == 403


# ---------- P-8：列表带计数 ----------


async def test_list_includes_request_count(db_session, client, make_headers):
    """P-8/FR-010：计数 = 该项目下「我可见」（我创建的 + 分派给我的）需求数。

    分派给我的那一条必须计数——经手人的下拉里看到「这个项目有 2 件活」，
    其中一件正是派给他的，少算会让人以为漏了。
    """
    owner = await _user(db_session)
    worker = await _user(db_session)
    project = await _make_project(db_session, owner, name="计一计")

    # owner 名下 2 条归属该项目的需求 + 1 条无归属
    for i in range(2):
        db_session.add(
            Request(
                owner_id=owner.id,
                title=f"归属{i}",
                project_id=project.id,
                project_name=project.name,
            )
        )
    db_session.add(Request(owner_id=owner.id, title="无归属"))
    await db_session.commit()

    resp = await client.get(PROJECTS_URL, headers=await make_headers(owner))
    item = next(p for p in resp.json()["items"] if p["id"] == project.id)
    assert item["request_count"] == 2

    # worker 视角（scope=relevant）：派给他的需求所属的项目也要出现，
    # 且计数只算他可见的那 1 条——接收人的筛选下拉必须含「派给我活的项目」
    req = Request(
        owner_id=owner.id,
        title="派给工人的",
        project_id=project.id,
        project_name=project.name,
        assignee_id=worker.id,
        status="pending",
    )
    db_session.add(req)
    await db_session.commit()

    resp = await client.get(
        f"{PROJECTS_URL}?scope=relevant", headers=await make_headers(worker)
    )
    item = next(p for p in resp.json()["items"] if p["id"] == project.id)
    assert item["request_count"] == 1

    # 管理页语义（scope=mine）：worker 自己没有项目，列表为空
    own = await client.get(PROJECTS_URL, headers=await make_headers(worker))
    assert own.json()["items"] == []


# ---------- P-9：边界 422 ----------


async def test_name_boundaries_422(db_session, client, make_headers):
    """P-9：空/纯空白/超长 → 422（schema 层拦）。"""
    owner = await _user(db_session)
    headers = await make_headers(owner)
    for bad in ("", "   ", "x" * 101):
        resp = await client.post(PROJECTS_URL, json={"name": bad}, headers=headers)
        assert resp.status_code == 422, f"{bad[:10]!r} 应 422，实得 {resp.status_code}"


# ---------- 审计（FR-009） ----------


async def test_create_writes_audit(db_session, client, make_headers):
    """FR-009：建项目落审计 project_create。"""
    owner = await _user(db_session)
    resp = await client.post(
        PROJECTS_URL, json={"name": "审计项目"}, headers=await make_headers(owner)
    )
    assert resp.status_code == 201
    after = int(
        (
            await db_session.execute(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.action == "project_create")
            )
        ).scalar()
    )
    assert after >= 1


# ---------- P-5：改名同步快照 ----------


async def test_rename_syncs_request_snapshot(db_session, client, make_headers):
    """P-5/R-04：项目改名 → 名下需求的 project_name 同事务变。"""
    owner = await _user(db_session)
    project = await _make_project(db_session, owner, name="旧名")
    req = Request(
        owner_id=owner.id,
        title="快照需求",
        project_id=project.id,
        project_name="旧名",
    )
    db_session.add(req)
    await db_session.commit()

    resp = await client.patch(
        f"{PROJECTS_URL}/{project.id}",
        json={"name": "新名"},
        headers=await make_headers(owner),
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "新名"

    # 直接查列绕身份映射（delete/commit 后的陈旧值教训，同 schema 测试）
    name = (
        await db_session.execute(
            select(Request.project_name).where(Request.id == req.id)
        )
    ).scalar()
    assert name == "新名"


# ---------- P-6：归档只拦新建 ----------


async def test_archive_keeps_existing_untouched(db_session, client, make_headers):
    """P-6/R-03（项目侧）：归档不改动任何既有需求的归属与状态。

    「归档后新建被拒（400）」在 test_request_project_link.py——它依赖 3b 的
    request_service 校验，本阶段先锁项目侧不级联。
    """
    owner = await _user(db_session)
    project = await _make_project(db_session, owner, name="归档我")
    req = Request(
        owner_id=owner.id,
        title="归档前的",
        project_id=project.id,
        project_name=project.name,
        status="pending",
    )
    db_session.add(req)
    await db_session.commit()

    resp = await client.patch(
        f"{PROJECTS_URL}/{project.id}",
        json={"status": "archived"},
        headers=await make_headers(owner),
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "archived"

    # 既有归属与状态都不变
    row = (
        await db_session.execute(
            select(Request.project_id, Request.status).where(Request.id == req.id)
        )
    ).one()
    assert row.project_id == project.id
    assert row.status == "pending"

    # 恢复 active（归档不是单行道）
    back = await client.patch(
        f"{PROJECTS_URL}/{project.id}",
        json={"status": "active"},
        headers=await make_headers(owner),
    )
    assert back.json()["status"] == "active"


# ---------- P-7：删除级联置空 ----------


async def test_delete_project_keeps_requests_and_clears_link(
    db_session, client, make_headers
):
    """P-7/R-02：删除 → 需求保留、归属两列清空；审计记 request_count。"""
    owner = await _user(db_session)
    project = await _make_project(db_session, owner, name="删了我")
    reqs = []
    for i in range(3):
        r = Request(
            owner_id=owner.id,
            title=f"幸存{i}",
            project_id=project.id,
            project_name=project.name,
        )
        db_session.add(r)
        reqs.append(r)
    await db_session.commit()

    resp = await client.delete(
        f"{PROJECTS_URL}/{project.id}", headers=await make_headers(owner)
    )
    assert resp.status_code == 204

    rows = (
        await db_session.execute(
            select(Request.project_id, Request.project_name).where(
                Request.owner_id == owner.id
            )
        )
    ).all()
    assert len(rows) == 3  # 一条没删
    assert all(pid is None and pname is None for pid, pname in rows)

    audit = (
        (
            await db_session.execute(
                select(AuditLog.detail)
                .where(AuditLog.action == "project_delete")
                .order_by(AuditLog.created_at.desc())
            )
        )
        .scalars()
        .first()
    )
    assert audit["request_count"] == 3
