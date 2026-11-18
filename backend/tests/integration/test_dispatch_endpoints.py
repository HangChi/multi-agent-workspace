"""007 accept/reject 端点的 API 层测试（T016 接线验证）。

服务级逻辑在 test_dispatch_actions.py；这里只锁 HTTP 层的三件事：
状态码映射（202/404/409/422/403）、鉴权与角色门（viewer 403）、
审计确实落库。setup 全部走 db_session + commit（API 用独立 session，
看不到未提交数据——项目既有约定）。
"""

import uuid

import pytest
from sqlalchemy import func, select

from app.core.audit import REQUEST_DISPATCH_ACTIONS
from app.models.audit_log import AuditLog
from app.models.request import Request
from app.models.user import User
from app.services import dispatch_service

pytestmark = pytest.mark.integration


async def _user(db, role: str = "member") -> User:
    user = User(
        email=f"api-{uuid.uuid4().hex[:8]}@test.com",
        password_hash="x" * 60,
        role=role,
        status="active",
    )
    db.add(user)
    await db.commit()
    return user


async def _pending_request(db, owner: User, worker: User) -> Request:
    req = Request(owner_id=owner.id, title=f"API-{uuid.uuid4().hex[:6]}")
    db.add(req)
    await db.flush()
    await dispatch_service.dispatch_request(
        db, request_id=req.id, actor_id=owner.id, target_user_id=worker.id
    )
    await db.commit()
    return req


async def test_accept_endpoint_returns_202(db_session, client, make_headers):
    """AC-1 端点层：当前接收人 accept → 202 accepted。"""
    owner = await _user(db_session)
    worker = await _user(db_session)
    req = await _pending_request(db_session, owner, worker)

    resp = await client.post(
        f"/api/v1/requests/{req.id}/accept", headers=await make_headers(worker)
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body["request_id"] == req.id
    assert body["status"] == "accepted"


async def test_accept_endpoint_stranger_404(db_session, client, make_headers):
    """AC-3 端点层：陌生人 accept → 404（不暴露存在性）。"""
    owner = await _user(db_session)
    worker = await _user(db_session)
    stranger = await _user(db_session)
    req = await _pending_request(db_session, owner, worker)

    resp = await client.post(
        f"/api/v1/requests/{req.id}/accept", headers=await make_headers(stranger)
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"


async def test_accept_endpoint_viewer_403(db_session, client, make_headers):
    """viewer 不能接收（与建需求同权）。"""
    owner = await _user(db_session)
    worker = await _user(db_session)
    req = await _pending_request(db_session, owner, worker)
    viewer = await _user(db_session, role="viewer")

    resp = await client.post(
        f"/api/v1/requests/{req.id}/accept", headers=await make_headers(viewer)
    )
    assert resp.status_code == 403


async def test_reject_endpoint_requires_reason(db_session, client, make_headers):
    """RJ-3 端点层：缺原因 → 422（schema 层拦截，不进 service）。"""
    owner = await _user(db_session)
    worker = await _user(db_session)
    req = await _pending_request(db_session, owner, worker)

    resp = await client.post(
        f"/api/v1/requests/{req.id}/reject",
        json={},
        headers=await make_headers(worker),
    )
    assert resp.status_code == 422


async def test_reject_endpoint_returns_202_and_audits(db_session, client, make_headers):
    """RJ-1 端点层：附原因退回 → 202 + 审计落库（FR-013）。"""
    owner = await _user(db_session)
    worker = await _user(db_session)
    req = await _pending_request(db_session, owner, worker)

    before = int(
        (
            await db_session.execute(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.target_id == req.id)
            )
        ).scalar()
    )
    resp = await client.post(
        f"/api/v1/requests/{req.id}/reject",
        json={"reason": "这周排期已满"},
        headers=await make_headers(worker),
    )
    assert resp.status_code == 202
    assert resp.json()["status"] == "created"

    after = int(
        (
            await db_session.execute(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.target_id == req.id)
            )
        ).scalar()
    )
    assert after == before + 1
    row = (
        (
            await db_session.execute(
                select(AuditLog)
                .where(AuditLog.target_id == req.id)
                .order_by(AuditLog.created_at.desc())
            )
        )
        .scalars()
        .first()
    )
    assert row.action == "request_reject"
    assert row.action in REQUEST_DISPATCH_ACTIONS


async def test_reject_running_409(db_session, client, make_headers):
    """RJ-4 端点层：执行中退回 → 409。"""
    owner = await _user(db_session)
    worker = await _user(db_session)
    req = Request(
        owner_id=owner.id, title="执行中", status="running", assignee_id=worker.id
    )
    db_session.add(req)
    await db_session.commit()

    resp = await client.post(
        f"/api/v1/requests/{req.id}/reject",
        json={"reason": "想退"},
        headers=await make_headers(worker),
    )
    assert resp.status_code == 409
