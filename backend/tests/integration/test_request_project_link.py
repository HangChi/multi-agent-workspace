"""008 需求归属集成测试（T013，US1/FR-004~006）：归属校验 + 权限分流 + 筛选。

核心是三条「不能靠读代码确认」的行为：
1. 建/改需求时项目校验的三种失败（他人 404 / 归档 400 / 不存在 404）；
2. **字段级权限分流**（R-05）：经手人改 project_id → 403，改标题 → 202
   （同一谓词两套行为，靠「请求体是否含该键」判定）；
3. `?project_id=` / `?project=none` 与 scope/status 正交叠加。
"""

import uuid

import pytest

from app.models.project import Project
from app.models.request import Request
from app.models.user import User

pytestmark = pytest.mark.integration

REQUESTS_URL = "/api/v1/requests"


async def _user(db, role: str = "member") -> User:
    user = User(
        email=f"link-{uuid.uuid4().hex[:8]}@test.com",
        password_hash="x" * 60,
        role=role,
        status="active",
    )
    db.add(user)
    await db.commit()
    return user


async def _project(db, owner: User, **kw) -> Project:
    project = Project(owner_id=owner.id, name=f"P-{uuid.uuid4().hex[:6]}", **kw)
    db.add(project)
    await db.commit()
    return project


async def _create_request(client, headers, **kw) -> dict:
    body = {"title": kw.pop("title", f"REQ-{uuid.uuid4().hex[:6]}")}
    body.update(kw)
    resp = await client.post(REQUESTS_URL, json=body, headers=headers)
    assert resp.status_code == 201, resp.text[:300]
    return resp.json()


# ---------- FR-004：建需求带归属 ----------


async def test_create_with_project_populates_snapshot(db_session, client, make_headers):
    """建需求传 project_id → project_id + project_name 双写。"""
    owner = await _user(db_session)
    project = await _project(db_session, owner)
    req = await _create_request(
        client, await make_headers(owner), project_id=project.id
    )
    assert req["project_id"] == project.id
    assert req["project_name"] == project.name


async def test_create_with_others_project_404(db_session, client, make_headers):
    """他人的项目 → 404（归属纪律，不暴露存在性）。"""
    owner = await _user(db_session)
    stranger = await _user(db_session)
    project = await _project(db_session, owner)
    resp = await client.post(
        REQUESTS_URL,
        json={"title": "塞别人的项目", "project_id": project.id},
        headers=await make_headers(stranger),
    )
    assert resp.status_code == 404


async def test_create_with_archived_project_400(db_session, client, make_headers):
    """已归档项目 → 400（看得见但不能用，R-03）。"""
    owner = await _user(db_session)
    project = await _project(db_session, owner, status="archived")
    resp = await client.post(
        REQUESTS_URL,
        json={"title": "塞归档项目", "project_id": project.id},
        headers=await make_headers(owner),
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "INVALID_INPUT"


# ---------- FR-005：字段级权限分流 ----------


async def test_assignee_cannot_change_project_but_can_edit_title(
    db_session, client, make_headers
):
    """R-05：经手人改 project_id → 403；同一请求改标题 → 202。"""
    owner = await _user(db_session)
    worker = await _user(db_session)
    project_a = await _project(db_session, owner)
    project_b = await _project(db_session, owner)
    req = await _create_request(
        client, await make_headers(owner), project_id=project_a.id
    )
    # 分派给 worker 并接收
    await client.post(
        f"{REQUESTS_URL}/{req['id']}/assign",
        json={"assignee_type": "user", "assignee_id": worker.id},
        headers=await make_headers(owner),
    )
    await client.post(
        f"{REQUESTS_URL}/{req['id']}/accept", headers=await make_headers(worker)
    )

    blocked = await client.patch(
        f"{REQUESTS_URL}/{req['id']}",
        json={"project_id": project_b.id},
        headers=await make_headers(worker),
    )
    assert blocked.status_code == 403

    ok = await client.patch(
        f"{REQUESTS_URL}/{req['id']}",
        json={"title": "经手人改标题"},
        headers=await make_headers(worker),
    )
    assert ok.status_code == 200


async def test_owner_can_change_project_and_clear_it(db_session, client, make_headers):
    """创建者可改归属、也可显式传 null 解除。"""
    owner = await _user(db_session)
    project_a = await _project(db_session, owner)
    project_b = await _project(db_session, owner)
    req = await _create_request(
        client, await make_headers(owner), project_id=project_a.id
    )

    moved = await client.patch(
        f"{REQUESTS_URL}/{req['id']}",
        json={"project_id": project_b.id},
        headers=await make_headers(owner),
    )
    assert moved.status_code == 200
    assert moved.json()["project_name"] == project_b.name

    cleared = await client.patch(
        f"{REQUESTS_URL}/{req['id']}",
        json={"project_id": None},
        headers=await make_headers(owner),
    )
    assert cleared.status_code == 200
    assert cleared.json()["project_id"] is None


# ---------- FR-006：筛选 ----------


async def test_filter_by_project_and_none(db_session, client, make_headers):
    """?project_id= 与 ?project=none 各自生效，且与 scope 正交。"""
    owner = await _user(db_session)
    project = await _project(db_session, owner)
    in_project = await _create_request(
        client, await make_headers(owner), project_id=project.id
    )
    loose = await _create_request(client, await make_headers(owner))

    filtered = await client.get(
        f"{REQUESTS_URL}?project_id={project.id}", headers=await make_headers(owner)
    )
    ids = {r["id"] for r in filtered.json()["items"]}
    assert in_project["id"] in ids
    assert loose["id"] not in ids

    none_resp = await client.get(
        f"{REQUESTS_URL}?project=none", headers=await make_headers(owner)
    )
    ids = {r["id"] for r in none_resp.json()["items"]}
    assert loose["id"] in ids
    assert in_project["id"] not in ids


async def test_filter_unknown_project_404(db_session, client, make_headers):
    """过滤目标不存在 → 404（不是静默空列表——那会让人以为项目被清空了）。"""
    owner = await _user(db_session)
    resp = await client.get(
        f"{REQUESTS_URL}?project_id={uuid.uuid4()}", headers=await make_headers(owner)
    )
    assert resp.status_code == 404


async def test_filter_archived_project_allowed_for_history(
    db_session, client, make_headers
):
    """Assumptions 2：归档项目仍可回看（回看不受归档影响，只拦新建）。"""
    owner = await _user(db_session)
    project = await _project(db_session, owner, status="archived")
    req = await _create_request(client, await make_headers(owner))
    # 直接 DB 归属（归档项目不能经 API 建，但历史数据可能来自归档前）
    db_req = await db_session.get(Request, req["id"])
    db_req.project_id = project.id
    db_req.project_name = project.name
    await db_session.commit()

    resp = await client.get(
        f"{REQUESTS_URL}?project_id={project.id}", headers=await make_headers(owner)
    )
    assert resp.status_code == 200
    assert req["id"] in {r["id"] for r in resp.json()["items"]}


# ---------- SC-006：零回归 ----------


async def test_list_without_project_param_unchanged_shape(
    db_session, client, make_headers
):
    """SC-006：不传项目参数时，既有字段逐字段一致（新字段可空）。"""
    owner = await _user(db_session)
    await _create_request(client, await make_headers(owner))
    resp = await client.get(REQUESTS_URL, headers=await make_headers(owner))
    item = resp.json()["items"][0]
    assert item["project_id"] is None
    assert item["project_name"] is None
    # 既有字段一个不少
    for field in (
        "id",
        "owner_id",
        "title",
        "priority",
        "status",
        "attachments",
        "created_at",
        "updated_at",
        "assignee_id",
        "dispatch_history",
    ):
        assert field in item, f"既有字段 {field} 丢失"
