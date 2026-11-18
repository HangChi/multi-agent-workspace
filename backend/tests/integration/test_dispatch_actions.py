"""007 分派动作集成测试（T014，US1/US2）：accept / reject / dispatch 服务级全用例。

这里锁的是**不能靠单测糊过去**的部分：真实数据库上的状态迁移、行级锁下的
并发赢家、通知与历史的落库、404/409/422 的分层。

每个用例跑完回滚（db_session 自带），但**提交过的数据不回收**——
断言一律用「相对量」而不是绝对计数（同 006 约定）。

注意：3a 只交付服务逻辑与 accept/reject 端点；dispatch（拖到员工）的端点
接线在 3b，本文件用**服务级调用**覆盖 dispatch/reassign（RJ-5）。
"""

import asyncio
import uuid

import pytest
from sqlalchemy import func, select

from app.models.notification import Notification
from app.models.request import Request
from app.models.request_dispatch import RequestDispatch
from app.models.user import User
from app.services import dispatch_service

pytestmark = pytest.mark.integration


# ---------- 辅助 ----------


async def _user(db) -> User:
    user = User(
        email=f"act-{uuid.uuid4().hex[:8]}@test.com",
        password_hash="x" * 60,
        role="member",
        status="active",
    )
    db.add(user)
    await db.flush()
    return user


async def _request(db, owner: User, *, status="created", assignee=None) -> Request:
    req = Request(
        owner_id=owner.id,
        title=f"需求-{uuid.uuid4().hex[:6]}",
        status=status,
        assignee_id=assignee.id if assignee else None,
    )
    db.add(req)
    await db.flush()
    return req


async def _dispatch(db, *, request: Request, actor: User, target: User) -> None:
    """服务级分派（3b 才会接上 assign 端点；先在这里驱动经手链）。"""
    await dispatch_service.dispatch_request(
        db, request_id=request.id, actor_id=actor.id, target_user_id=target.id
    )
    await db.commit()


async def _notif_count(db, user_id: str, *, type_: str | None = None) -> int:
    stmt = (
        select(func.count())
        .select_from(Notification)
        .where(Notification.user_id == user_id)
    )
    if type_:
        stmt = stmt.where(Notification.type == type_)
    return int((await db.execute(stmt)).scalar())


# ---------- AC：接收 ----------


async def test_accept_by_current_assignee(db_session):
    """AC-1：当前接收人接收 → accepted + 分派人收到通知 + 历史一条 accept。"""
    owner = await _user(db_session)
    worker = await _user(db_session)
    req = await _request(db_session, owner)
    await _dispatch(db_session, request=req, actor=owner, target=worker)

    before = await _notif_count(db_session, owner.id)
    result = await dispatch_service.accept_request(
        db_session, request_id=req.id, actor_id=worker.id
    )
    await db_session.commit()

    assert result.status == "accepted"
    assert result.accepted_at is not None
    assert await _notif_count(db_session, owner.id) == before + 1
    notif = (
        (
            await db_session.execute(
                select(Notification)
                .where(Notification.user_id == owner.id)
                .order_by(Notification.created_at.desc())
            )
        )
        .scalars()
        .first()
    )
    assert notif.type == "request_accepted"
    history = (
        (
            await db_session.execute(
                select(RequestDispatch.action).where(
                    RequestDispatch.request_id == req.id
                )
            )
        )
        .scalars()
        .all()
    )
    assert history.count("accept") == 1


async def test_accept_self_dispatched(db_session):
    """AC-2：自派（assignee == owner）后可自行接收。"""
    owner = await _user(db_session)
    req = await _request(db_session, owner)
    await _dispatch(db_session, request=req, actor=owner, target=owner)

    result = await dispatch_service.accept_request(
        db_session, request_id=req.id, actor_id=owner.id
    )
    await db_session.commit()
    assert result.status == "accepted"


async def test_accept_by_stranger_is_404(db_session):
    """AC-3：非接收人非创建者 → 404（不暴露存在性），状态不变。

    状态不变用 select 直接查库验证——rollback 会把未提交的 setup 数据
    一起回滚，get() 又会撞上过期属性的惰性加载（两个都曾在此翻车）。
    """
    owner = await _user(db_session)
    worker = await _user(db_session)
    stranger = await _user(db_session)
    req = await _request(db_session, owner)
    await _dispatch(db_session, request=req, actor=owner, target=worker)

    from app.api.errors import AppError

    with pytest.raises(AppError) as exc:
        await dispatch_service.accept_request(
            db_session, request_id=req.id, actor_id=stranger.id
        )
    assert exc.value.code == "NOT_FOUND"
    current = (
        await db_session.execute(select(Request.status).where(Request.id == req.id))
    ).scalar()
    assert current == "pending"


async def test_accept_twice_is_409(db_session):
    """AC-4：重复接收 → 409（service 层按当前状态拦截，不靠状态机同态放行）。"""
    owner = await _user(db_session)
    worker = await _user(db_session)
    req = await _request(db_session, owner)
    await _dispatch(db_session, request=req, actor=owner, target=worker)
    await dispatch_service.accept_request(
        db_session, request_id=req.id, actor_id=worker.id
    )
    await db_session.commit()

    from app.api.errors import AppError

    with pytest.raises(AppError) as exc:
        await dispatch_service.accept_request(
            db_session, request_id=req.id, actor_id=worker.id
        )
    assert exc.value.code == "CONFLICT"


async def test_concurrent_accept_single_winner(db_session):
    """AC-5 / SC-004：并发接收同一 pending → 恰一个成功，历史仅一条 accept。

    行级锁（for_update）是唯一硬保证：两个事务都读到 pending，但第二个
    在锁等待后看到的状态已是 accepted，被 service 的 409 拦下。
    """
    owner = await _user(db_session)
    worker = await _user(db_session)
    req = await _request(db_session, owner)
    await _dispatch(db_session, request=req, actor=owner, target=worker)

    from app.api.errors import AppError

    async def _try_accept():
        # 每个并发走独立 session：同 session 会把第二次的读挡在身份映射里
        from tests.conftest import TestSession

        async with TestSession() as s2:
            try:
                await dispatch_service.accept_request(
                    s2, request_id=req.id, actor_id=worker.id
                )
                await s2.commit()
                return "ok"
            except AppError as e:
                await s2.rollback()
                return e.code

    results = await asyncio.gather(_try_accept(), _try_accept())
    assert sorted(results) == ["CONFLICT", "ok"]
    await db_session.commit()
    history = (
        (
            await db_session.execute(
                select(RequestDispatch.action).where(
                    RequestDispatch.request_id == req.id
                )
            )
        )
        .scalars()
        .all()
    )
    assert history.count("accept") == 1


# ---------- RJ：退回 ----------


async def test_reject_with_reason_returns_to_created(db_session):
    """RJ-1：附原因退回 → created + 三列清空 + 分派人收到含原因通知。"""
    owner = await _user(db_session)
    worker = await _user(db_session)
    req = await _request(db_session, owner)
    await _dispatch(db_session, request=req, actor=owner, target=worker)

    before = await _notif_count(db_session, owner.id)
    result = await dispatch_service.reject_request(
        db_session, request_id=req.id, actor_id=worker.id, reason="这周排期已满"
    )
    await db_session.commit()

    assert result.status == "created"
    assert result.assignee_id is None
    assert result.dispatched_at is None
    assert result.accepted_at is None
    assert await _notif_count(db_session, owner.id) == before + 1
    notif = (
        (
            await db_session.execute(
                select(Notification)
                .where(Notification.user_id == owner.id)
                .order_by(Notification.created_at.desc())
            )
        )
        .scalars()
        .first()
    )
    assert notif.type == "request_rejected"
    assert notif.payload["reason"] == "这周排期已满"
    history = (
        (
            await db_session.execute(
                select(RequestDispatch).where(RequestDispatch.request_id == req.id)
            )
        )
        .scalars()
        .all()
    )
    rejected = [h for h in history if h.action == "reject"]
    assert len(rejected) == 1
    assert rejected[0].reason == "这周排期已满"


async def test_reject_by_owner_recalls_dispatch(db_session):
    """RJ-2：创建者撤回分派 → created + 通知发给被撤接收人。"""
    owner = await _user(db_session)
    worker = await _user(db_session)
    req = await _request(db_session, owner)
    await _dispatch(db_session, request=req, actor=owner, target=worker)

    before = await _notif_count(db_session, worker.id)
    result = await dispatch_service.reject_request(
        db_session, request_id=req.id, actor_id=owner.id, reason="派错了，我来处理"
    )
    await db_session.commit()

    assert result.status == "created"
    assert result.assignee_id is None
    assert await _notif_count(db_session, worker.id) == before + 1


async def test_reject_running_request_is_409(db_session):
    """RJ-4 / FR-005：执行中的需求不可退回，状态与接收人都不变。"""
    owner = await _user(db_session)
    worker = await _user(db_session)
    req = await _request(db_session, owner, status="running", assignee=worker)
    await db_session.commit()

    from app.api.errors import AppError

    with pytest.raises(AppError) as exc:
        await dispatch_service.reject_request(
            db_session, request_id=req.id, actor_id=worker.id, reason="想退"
        )
    assert exc.value.code == "CONFLICT"
    # 不 rollback：service 在抛错前没有任何写入，事务仍可用（实测 rollback 反而
    # 会让 session 丢 greenlet 上下文，后续 execute 报 MissingGreenlet）。
    row = (
        await db_session.execute(
            select(Request.status, Request.assignee_id).where(Request.id == req.id)
        )
    ).one()
    assert row.status == "running"
    assert row.assignee_id == worker.id


async def test_reject_then_dispatch_records_as_dispatch(db_session):
    """RJ-5：退回后再次分派记 dispatch（首次），不是 reassign。

    assignee 已清空，「从无人认领到有人」永远是首次分派语义。
    """
    owner = await _user(db_session)
    worker = await _user(db_session)
    other = await _user(db_session)
    req = await _request(db_session, owner)
    await _dispatch(db_session, request=req, actor=owner, target=worker)
    await dispatch_service.reject_request(
        db_session, request_id=req.id, actor_id=worker.id, reason="不熟这块"
    )
    await db_session.commit()

    await _dispatch(db_session, request=req, actor=owner, target=other)
    actions = (
        (
            await db_session.execute(
                select(RequestDispatch.action).where(
                    RequestDispatch.request_id == req.id
                )
            )
        )
        .scalars()
        .all()
    )
    assert actions.count("dispatch") == 2
    assert actions.count("reassign") == 0
