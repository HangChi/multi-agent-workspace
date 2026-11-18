"""007 数据模型集成测试（T012）：迁移 0011 的 schema 断言 + 不变量落库。

锁的是「不能靠单测糊过去」的部分：枚举新值在真实 PG 上可用、
部分索引真实存在、级联/SET NULL 行为与模型注释一致、不变量在
ORM 写入路径上成立。
"""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select, text

from app.models.notification import Notification
from app.models.request import Request
from app.models.request_dispatch import RequestDispatch
from app.models.user import User

pytestmark = pytest.mark.integration


async def _user(db) -> User:
    user = User(
        email=f"dispatch-{uuid.uuid4().hex[:8]}@test.com",
        password_hash="x" * 60,
        role="member",
        status="active",
    )
    db.add(user)
    await db.flush()
    return user


# ---------- 枚举新值可用（autocommit_block 迁移生效） ----------


async def test_request_status_accepts_pending_and_accepted(db_session):
    """迁移后 PG 的 request_status 枚举必须接受 pending/accepted。

    ADD VALUE 与使用新值不同事务的坑（data-model §5）：这条用例在真实
    连接上 INSERT 两个新状态，迁移写错会在这里暴露。
    """
    owner = await _user(db_session)
    assignee = await _user(db_session)
    req = Request(
        owner_id=owner.id,
        title="枚举新值",
        status="pending",
        assignee_id=assignee.id,
    )
    db_session.add(req)
    await db_session.flush()

    req.status = "accepted"
    await db_session.flush()
    assert req.status == "accepted"


# ---------- 索引存在（收件箱热点路径） ----------


async def test_indexes_exist(db_session):
    """ix_requests_assignee_status 与未读部分索引必须真实存在。"""
    rows = (
        await db_session.execute(
            text(
                "SELECT indexname FROM pg_indexes "
                "WHERE indexname IN ('ix_requests_assignee_status', "
                "'ix_notifications_user_unread', 'ix_notifications_user_id', "
                "'ix_request_dispatches_request_id')"
            )
        )
    ).scalars()
    names = set(rows)
    assert {
        "ix_requests_assignee_status",
        "ix_notifications_user_unread",
        "ix_notifications_user_id",
        "ix_request_dispatches_request_id",
    } <= names


async def test_unread_index_is_partial(db_session):
    """未读索引必须是部分索引（WHERE read_at IS NULL）——已读行不入索引。"""
    definition = (
        await db_session.execute(
            text(
                "SELECT pg_get_expr(indpred, indrelid) FROM pg_index "
                "WHERE indexrelid = "
                "'ix_notifications_user_unread'::regclass"
            )
        )
    ).scalar()
    assert definition is not None
    assert "read_at IS NULL" in definition.replace("(", "").replace(")", "")


# ---------- 级联与 SET NULL 行为 ----------


async def test_dispatch_history_cascades_with_request(db_session):
    """删除需求 → 经手历史随之删除（ON DELETE CASCADE）。"""
    owner = await _user(db_session)
    assignee = await _user(db_session)
    req = Request(
        owner_id=owner.id, title="级联", status="pending", assignee_id=assignee.id
    )
    db_session.add(req)
    await db_session.flush()
    db_session.add(
        RequestDispatch(
            request_id=req.id,
            action="dispatch",
            actor_id=owner.id,
            to_assignee_id=assignee.id,
        )
    )
    await db_session.commit()

    await db_session.delete(req)
    await db_session.commit()
    left = (
        (
            await db_session.execute(
                select(RequestDispatch).where(RequestDispatch.request_id == req.id)
            )
        )
        .scalars()
        .all()
    )
    assert left == []


async def test_assignee_set_null_on_user_delete(db_session):
    """删除接收人 → 需求保留、assignee_id 置空（ON DELETE SET NULL）。

    需求是「已经发生过的事」，不随经手人账号消失；历史行保留。
    """
    owner = await _user(db_session)
    assignee = await _user(db_session)
    req = Request(
        owner_id=owner.id,
        title="离职交接",
        status="pending",
        assignee_id=assignee.id,
    )
    db_session.add(req)
    await db_session.flush()
    db_session.add(
        RequestDispatch(
            request_id=req.id,
            action="dispatch",
            actor_id=owner.id,
            to_assignee_id=assignee.id,
        )
    )
    await db_session.commit()

    await db_session.delete(assignee)
    await db_session.commit()

    # ⚠️ 必须 refresh：SET NULL 是数据库做的，ORM 身份 map 里还留着旧值，
    # 直接读属性会看到「约束没生效」的假象（同 005 测试的处理）。
    await db_session.refresh(req)
    assert req.assignee_id is None
    history = (
        (
            await db_session.execute(
                select(RequestDispatch).where(RequestDispatch.request_id == req.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(history) == 1  # 历史是事实，账号消失不抹掉


async def test_notification_cascades_with_user(db_session):
    """删除用户 → 其通知随之删除（个人收件箱数据，无跨用户审计价值）。"""
    user = await _user(db_session)
    db_session.add(
        Notification(
            user_id=user.id,
            type="request_dispatched",
            title="有人给你派了活",
            payload={},
        )
    )
    await db_session.commit()

    await db_session.delete(user)
    await db_session.commit()
    left = (
        (
            await db_session.execute(
                select(Notification).where(Notification.user_id == user.id)
            )
        )
        .scalars()
        .all()
    )
    assert left == []


# ---------- 不变量落库（dispatch 服务写入口的预演） ----------


async def test_dispatch_invariants_hold_on_orm_write(db_session):
    """ORU 写入路径上：pending 有 assignee、accepted 有 accepted_at。"""
    from app.orchestrator.request_state_machine import (
        assert_dispatch_invariants,
    )

    owner = await _user(db_session)
    assignee = await _user(db_session)
    req = Request(
        owner_id=owner.id, title="不变量", status="pending", assignee_id=assignee.id
    )
    db_session.add(req)
    await db_session.flush()
    assert_dispatch_invariants(req)

    req.status = "accepted"
    req.accepted_at = datetime(2026, 11, 18, tzinfo=UTC)
    await db_session.flush()
    assert_dispatch_invariants(req)

    # 退回：清空三列后回到 created，不变量仍成立
    req.status = "created"
    req.assignee_id = None
    req.dispatched_at = None
    req.accepted_at = None
    await db_session.flush()
    assert_dispatch_invariants(req)
