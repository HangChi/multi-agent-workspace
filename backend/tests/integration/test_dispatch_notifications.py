"""007 人员端点与通知 API 测试（T023/T025，US4/US5）。

- GET /users/options：在职最小投影（U-1..U-4）
- GET /notifications + POST /notifications/read：列表/未读/已读/越权（N-1..N-4）

setup 全部 commit（API 走独立 session）。
"""

import uuid

import pytest
from sqlalchemy import func, select

from app.models.notification import Notification
from app.models.request import Request
from app.models.user import User
from app.services import dispatch_service

pytestmark = pytest.mark.integration


async def _user(db, role: str = "member", status: str = "active") -> User:
    user = User(
        email=f"ntf-{uuid.uuid4().hex[:8]}@test.com",
        password_hash="x" * 60,
        role=role,
        status=status,
    )
    db.add(user)
    await db.commit()
    return user


async def _dispatched(db, owner: User, worker: User) -> Request:
    req = Request(owner_id=owner.id, title=f"N-{uuid.uuid4().hex[:6]}")
    db.add(req)
    await db.flush()
    await dispatch_service.dispatch_request(
        db, request_id=req.id, actor_id=owner.id, target_user_id=worker.id
    )
    await db.commit()
    return req


# ---------- U：人员选择端点 ----------


async def test_users_options_member_sees_active_only(db_session, client, make_headers):
    """U-1/U-2：member 可用；只返回在职用户；字段最小（id/email/role）。"""
    active = await _user(db_session)
    await _user(db_session, status="disabled")
    resp = await client.get("/api/v1/users/options", headers=await make_headers(active))
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert all(set(item) == {"id", "email", "role"} for item in items)
    emails = {item["email"] for item in items}
    assert active.email in emails
    assert not any("disabled" in e for e in emails)


async def test_users_options_viewer_403(db_session, client, make_headers):
    """U-3：viewer 403（与建需求同权）。"""
    viewer = await _user(db_session, role="viewer")
    resp = await client.get("/api/v1/users/options", headers=await make_headers(viewer))
    assert resp.status_code == 403


async def test_users_options_requires_auth(client):
    """U-4：未登录 401。"""
    resp = await client.get("/api/v1/users/options")
    assert resp.status_code == 401


# ---------- N：通知读写 ----------


async def test_unread_notification_after_dispatch(db_session, client, make_headers):
    """N-1：分派后接收人有 1 条未读；unread_only=true 能筛出。"""
    owner = await _user(db_session)
    worker = await _user(db_session)
    await _dispatched(db_session, owner, worker)

    resp = await client.get(
        "/api/v1/notifications?unread_only=true",
        headers=await make_headers(worker),
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["type"] == "request_dispatched"
    assert items[0]["read_at"] is None
    assert items[0]["title"]  # 标题快照非空


async def test_mark_all_read_then_idempotent(db_session, client, make_headers):
    """N-2：全部已读 → 未读空；再点 updated=0（幂等）。"""
    owner = await _user(db_session)
    worker = await _user(db_session)
    await _dispatched(db_session, owner, worker)
    headers = await make_headers(worker)

    resp = await client.post("/api/v1/notifications/read", json={}, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["updated"] == 1

    again = await client.get("/api/v1/notifications?unread_only=true", headers=headers)
    assert again.json()["items"] == []

    second = await client.post("/api/v1/notifications/read", json={}, headers=headers)
    assert second.json()["updated"] == 0


async def test_mark_read_cannot_touch_others(db_session, client, make_headers):
    """N-3：传他人通知 id → updated=0（不越权已读）。"""
    owner = await _user(db_session)
    worker = await _user(db_session)
    stranger = await _user(db_session)
    await _dispatched(db_session, owner, worker)

    notif = (await db_session.execute(select(Notification).limit(1))).scalars().first()
    resp = await client.post(
        "/api/v1/notifications/read",
        json={"ids": [notif.id]},
        headers=await make_headers(stranger),
    )
    assert resp.json()["updated"] == 0
    # 直接查库计数（不用 expire_all——它会让 session 丢 greenlet 上下文，
    # 后续 execute 报 MissingGreenlet，Phase 3a 已踩过一次）
    still_unread = (
        await db_session.execute(
            select(func.count())
            .select_from(Notification)
            .where(Notification.read_at.is_(None), Notification.user_id == worker.id)
        )
    ).scalar()
    assert still_unread == 1


async def test_notification_chain_order(db_session, client, make_headers):
    """N-4：分派→接收→退回全链路，各方收到对应类型通知。

    通知对象口径（contracts §3/§4/§5）：分派通知发给**接收人**（worker），
    接收/退回通知发给**分派人**（owner）。
    """
    owner = await _user(db_session)
    worker = await _user(db_session)
    req = await _dispatched(db_session, owner, worker)

    await client.post(
        f"/api/v1/requests/{req.id}/accept", headers=await make_headers(worker)
    )
    await client.post(
        f"/api/v1/requests/{req.id}/reject",
        json={"reason": "排期已满"},
        headers=await make_headers(worker),
    )

    owner_n = (
        await client.get("/api/v1/notifications", headers=await make_headers(owner))
    ).json()["items"]
    # 接口契约是倒序（新的在上）；反转后即事件发生顺序
    owner_types = [n["type"] for n in reversed(owner_n)]
    assert owner_types == ["request_accepted", "request_rejected"]

    worker_n = (
        await client.get("/api/v1/notifications", headers=await make_headers(worker))
    ).json()["items"]
    # 分派通知只发给接收人；退回者是 worker 自己，不会再收自己的通知
    assert [n["type"] for n in worker_n] == ["request_dispatched"]


# ---------- FR-010：执行完成通知创建者 ----------


async def _notif_count(db, user_id: str, *, type_: str | None = None) -> int:
    """某用户的未读/指定类型通知数（按 user_id 过滤——API 提交的数据不随
    db_session 回滚，全局计数会漏进别的测试的数据）。"""
    stmt = (
        select(func.count())
        .select_from(Notification)
        .where(Notification.user_id == user_id)
    )
    if type_:
        stmt = stmt.where(Notification.type == type_)
    return int((await db.execute(stmt)).scalar())


async def test_run_completed_notifies_owner_on_settle(db_session):
    """需求收敛终态时创建者收到 run_completed；重复 settle 不重复发。"""
    from app.models.run import Run
    from app.services.request_service import settle_request_status

    owner = await _user(db_session)
    req = Request(owner_id=owner.id, title="执行完成通知", status="running")
    db_session.add(req)
    await db_session.flush()
    db_session.add(
        Run(
            request_id=req.id,
            owner_id=owner.id,
            assignee_type="agent",
            assignee_id=str(uuid.uuid4()),
            strategy="direct",
            status="completed",
            cost_cents=10,
        )
    )
    await db_session.commit()

    before = await _notif_count(db_session, owner.id, type_="run_completed")
    assert await settle_request_status(db_session, req.id) == "completed"
    await db_session.commit()
    assert await _notif_count(db_session, owner.id, type_="run_completed") == before + 1

    # 幂等：需求已终态，再次 settle 直接返回 None，不再发通知
    assert await settle_request_status(db_session, req.id) is None
    await db_session.commit()
    assert await _notif_count(db_session, owner.id, type_="run_completed") == before + 1
