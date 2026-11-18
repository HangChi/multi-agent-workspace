"""009 依赖边端点集成测试（T007，D-1~D-12）：CRUD + 权限 + 成环。

判定顺序见 contracts §1.2：认证 → 角色 403 → schema 422 → 被依赖方 404
→ 自依赖 400 → 目标 404 → 重复 409 → 成环 409 → 审计 201。
"""

import uuid

import pytest
from sqlalchemy import select

from app.models.audit_log import AuditLog
from app.models.user import User

pytestmark = pytest.mark.integration

DEP_URL = "/api/v1/requests/{rid}/dependencies"


async def _user(db, role: str = "member") -> User:
    user = User(
        email=f"dep-api-{uuid.uuid4().hex[:8]}@test.com",
        password_hash="x" * 60,
        role=role,
        status="active",
    )
    db.add(user)
    await db.commit()
    return user


async def _request(client, headers, title: str | None = None) -> dict:
    resp = await client.post(
        "/api/v1/requests",
        json={"title": title or f"REQ-{uuid.uuid4().hex[:6]}"},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text[:300]
    return resp.json()


# ---------- D-1：加边 ----------


async def test_add_edge_returns_dependency_info(db_session, client, make_headers):
    """D-1：201 且响应带前置的标题与状态（前端不需要再查一次需求）。"""
    owner = await _user(db_session)
    headers = await make_headers(owner)
    a = await _request(client, headers, "前置需求")
    b = await _request(client, headers, "后续需求")

    resp = await client.post(
        DEP_URL.format(rid=b["id"]), json={"dependency_id": a["id"]}, headers=headers
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["dependency_id"] == a["id"]
    assert body["dependency_title"] == "前置需求"
    assert body["dependency_status"] == "created"

    # 列表可见
    listing = await client.get(DEP_URL.format(rid=b["id"]), headers=headers)
    assert [e["dependency_id"] for e in listing.json()["items"]] == [a["id"]]


# ---------- D-2/D-3/D-4：自依赖 / 目标不存在 / 重复 ----------


async def test_self_dependency_400(db_session, client, make_headers):
    """D-2：自依赖 400（service 先拦，CHECK 兜底）。"""
    owner = await _user(db_session)
    headers = await make_headers(owner)
    b = await _request(client, headers)
    resp = await client.post(
        DEP_URL.format(rid=b["id"]), json={"dependency_id": b["id"]}, headers=headers
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "INVALID_INPUT"


async def test_missing_dependency_404(db_session, client, make_headers):
    """D-3：前置目标不存在 → 404。"""
    owner = await _user(db_session)
    headers = await make_headers(owner)
    b = await _request(client, headers)
    resp = await client.post(
        DEP_URL.format(rid=b["id"]),
        json={"dependency_id": str(uuid.uuid4())},
        headers=headers,
    )
    assert resp.status_code == 404


async def test_duplicate_edge_409(db_session, client, make_headers):
    """D-4：重复边 409。"""
    owner = await _user(db_session)
    headers = await make_headers(owner)
    a = await _request(client, headers)
    b = await _request(client, headers)
    first = await client.post(
        DEP_URL.format(rid=b["id"]), json={"dependency_id": a["id"]}, headers=headers
    )
    assert first.status_code == 201
    second = await client.post(
        DEP_URL.format(rid=b["id"]), json={"dependency_id": a["id"]}, headers=headers
    )
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "CONFLICT"


# ---------- D-5/D-6：成环 ----------


async def test_direct_cycle_409(db_session, client, make_headers):
    """D-5：A 依赖 B 已存在 → 给 B 加「依赖 A」成环。"""
    owner = await _user(db_session)
    headers = await make_headers(owner)
    a = await _request(client, headers)
    b = await _request(client, headers)
    await client.post(
        DEP_URL.format(rid=a["id"]), json={"dependency_id": b["id"]}, headers=headers
    )
    resp = await client.post(
        DEP_URL.format(rid=b["id"]), json={"dependency_id": a["id"]}, headers=headers
    )
    assert resp.status_code == 409
    assert "循环依赖" in resp.json()["error"]["message"]


async def test_transitive_cycle_409(db_session, client, make_headers):
    """D-6：A→B→C 链，给 C 加「依赖 A」成环（BFS 二跳）。"""
    owner = await _user(db_session)
    headers = await make_headers(owner)
    a = await _request(client, headers)
    b = await _request(client, headers)
    c = await _request(client, headers)
    await client.post(
        DEP_URL.format(rid=a["id"]), json={"dependency_id": b["id"]}, headers=headers
    )
    await client.post(
        DEP_URL.format(rid=b["id"]), json={"dependency_id": c["id"]}, headers=headers
    )
    resp = await client.post(
        DEP_URL.format(rid=c["id"]), json={"dependency_id": a["id"]}, headers=headers
    )
    assert resp.status_code == 409


# ---------- D-7/D-8：删边 ----------


async def test_delete_edge(db_session, client, make_headers):
    """D-7：删边 204 且列表为空。"""
    owner = await _user(db_session)
    headers = await make_headers(owner)
    a = await _request(client, headers)
    b = await _request(client, headers)
    await client.post(
        DEP_URL.format(rid=b["id"]), json={"dependency_id": a["id"]}, headers=headers
    )
    resp = await client.delete(
        f"{DEP_URL.format(rid=b['id'])}/{a['id']}", headers=headers
    )
    assert resp.status_code == 204
    listing = await client.get(DEP_URL.format(rid=b["id"]), headers=headers)
    assert listing.json()["items"] == []


async def test_delete_missing_edge_404(db_session, client, make_headers):
    """D-8：删不存在的边 404。"""
    owner = await _user(db_session)
    headers = await make_headers(owner)
    b = await _request(client, headers)
    resp = await client.delete(
        f"{DEP_URL.format(rid=b['id'])}/{uuid.uuid4()}", headers=headers
    )
    assert resp.status_code == 404


# ---------- D-9/D-10：他人 / viewer ----------


async def test_others_request_edges_404(db_session, client, make_headers):
    """D-9：对他人的需求加边/查边/删边一律 404。"""
    owner = await _user(db_session)
    stranger = await _user(db_session)
    owner_headers = await make_headers(owner)
    a = await _request(client, owner_headers)
    b = await _request(client, owner_headers)

    stranger_headers = await make_headers(stranger)
    add = await client.post(
        DEP_URL.format(rid=b["id"]),
        json={"dependency_id": a["id"]},
        headers=stranger_headers,
    )
    assert add.status_code == 404
    listing = await client.get(DEP_URL.format(rid=b["id"]), headers=stranger_headers)
    assert listing.status_code == 404
    delete = await client.delete(
        f"{DEP_URL.format(rid=b['id'])}/{a['id']}", headers=stranger_headers
    )
    assert delete.status_code == 404


async def test_viewer_forbidden(db_session, client, make_headers):
    """D-10：viewer 对所有边端点 403。"""
    viewer = await _user(db_session, role="viewer")
    owner = await _user(db_session)
    headers = await make_headers(viewer)
    owner_headers = await make_headers(owner)
    a = await _request(client, owner_headers)
    b = await _request(client, owner_headers)

    add = await client.post(
        DEP_URL.format(rid=b["id"]), json={"dependency_id": a["id"]}, headers=headers
    )
    assert add.status_code == 403
    listing = await client.get(DEP_URL.format(rid=b["id"]), headers=headers)
    assert listing.status_code == 403


# ---------- D-11：经手人可删边（R-06） ----------


async def test_assignee_can_remove_edge(db_session, client, make_headers):
    """D-11/R-06：被 409 挡住的人（经手人）能自助删边，不用回去找创建者。"""
    owner = await _user(db_session)
    worker = await _user(db_session)
    owner_headers = await make_headers(owner)
    a = await _request(client, owner_headers)
    b = await _request(client, owner_headers)
    await client.post(
        DEP_URL.format(rid=b["id"]),
        json={"dependency_id": a["id"]},
        headers=owner_headers,
    )
    # 分派给 worker 并接收
    await client.post(
        f"/api/v1/requests/{b['id']}/assign",
        json={"assignee_type": "user", "assignee_id": worker.id},
        headers=owner_headers,
    )
    await client.post(
        f"/api/v1/requests/{b['id']}/accept", headers=await make_headers(worker)
    )

    resp = await client.delete(
        f"{DEP_URL.format(rid=b['id'])}/{a['id']}", headers=await make_headers(worker)
    )
    assert resp.status_code == 204


# ---------- D-12：反向视图 ----------


async def test_dependents_view(db_session, client, make_headers):
    """D-12：A 的 dependents 含 B（标题+状态）。"""
    owner = await _user(db_session)
    headers = await make_headers(owner)
    a = await _request(client, headers, "前置")
    b = await _request(client, headers, "后续")
    await client.post(
        DEP_URL.format(rid=b["id"]), json={"dependency_id": a["id"]}, headers=headers
    )
    resp = await client.get(f"/api/v1/requests/{a['id']}/dependents", headers=headers)
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert [i["request_id"] for i in items] == [b["id"]]
    # 反向视图复用 DependencyEdgeOut：request_id 是后续方，对端展示字段
    # （dependency_title/status）语义翻转为后续方的信息（schema docstring）
    assert items[0]["dependency_title"] == "后续"
    assert items[0]["dependency_status"] == "created"


# ---------- 审计（FR-011） ----------


async def test_add_and_remove_write_audit(db_session, client, make_headers):
    """FR-011：增删边各落一条审计，detail 带标题快照。"""
    owner = await _user(db_session)
    headers = await make_headers(owner)
    a = await _request(client, headers, "审计前置")
    b = await _request(client, headers)
    await client.post(
        DEP_URL.format(rid=b["id"]), json={"dependency_id": a["id"]}, headers=headers
    )
    await client.delete(f"{DEP_URL.format(rid=b['id'])}/{a['id']}", headers=headers)

    # 必须按 user_id 过滤：测试库是会话级共享的，不过滤会捞到其他
    # 测试文件留下的审计行（本特性首轮就踩到——detail 断言拿到别测试的标题）
    rows = (
        await db_session.execute(
            select(AuditLog.action, AuditLog.detail).where(
                AuditLog.action.in_(
                    ["request_dependency_add", "request_dependency_remove"]
                ),
                AuditLog.user_id == owner.id,
            )
        )
    ).all()
    actions = {action for action, _ in rows}
    assert actions == {"request_dependency_add", "request_dependency_remove"}
    detail = next(d for action, d in rows if action == "request_dependency_add")
    assert detail["dependency_title"] == "审计前置"
