"""009 数据模型集成测试（T004）：迁移 0013 的 schema 断言 + 级联行为。

锁的是「不能靠单测糊过去」的部分：UNIQUE/CHECK 两个约束在真实 PG 上
生效、两个方向的索引存在、删需求时其依赖边级联消失（门禁/反查都挂在
request_id 上，级联丢了边就等于结构静默损坏）。
"""

import uuid

import pytest
from sqlalchemy import text

from app.models.request import Request
from app.models.request_dependency import RequestDependency
from app.models.user import User

pytestmark = pytest.mark.integration


async def _user(db) -> User:
    user = User(
        email=f"dep-{uuid.uuid4().hex[:8]}@test.com",
        password_hash="x" * 60,
        role="member",
        status="active",
    )
    db.add(user)
    await db.flush()
    return user


async def _request(db, owner: User, title: str) -> Request:
    req = Request(owner_id=owner.id, title=title)
    db.add(req)
    await db.flush()
    return req


async def test_columns_and_constraints_exist(db_session):
    """列齐全；(request_id, dependency_id) 唯一约束存在。"""
    cols = (
        (
            await db_session.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'request_dependencies'"
                )
            )
        )
        .scalars()
        .all()
    )
    assert {"id", "request_id", "dependency_id", "created_by", "created_at"} <= set(
        cols
    )

    unique = (
        await db_session.execute(
            text(
                "SELECT count(*) FROM pg_constraint "
                "WHERE conrelid = 'request_dependencies'::regclass "
                "AND contype = 'u' AND conname = 'uq_request_dependencies_pair'"
            )
        )
    ).scalar()
    assert unique == 1


async def test_indexes_exist(db_session):
    """两个方向的索引都存在（门禁查 request_id，反查依赖方查 dependency_id）。"""
    names = set(
        (
            await db_session.execute(
                text(
                    "SELECT indexname FROM pg_indexes "
                    "WHERE indexname IN ('ix_request_dependencies_request_id', "
                    "'ix_request_dependencies_dependency_id')"
                )
            )
        )
        .scalars()
        .all()
    )
    assert {
        "ix_request_dependencies_request_id",
        "ix_request_dependencies_dependency_id",
    } <= names


async def test_self_dependency_rejected_by_check(db_session):
    """CHECK 约束在库层堵死自依赖（service 先拦 400，这里钉库级兜底）。"""
    owner = await _user(db_session)
    req = await _request(db_session, owner, "自依赖")
    db_session.add(
        RequestDependency(request_id=req.id, dependency_id=req.id, created_by=owner.id)
    )
    with pytest.raises(Exception):  # noqa: B017 - CheckViolation 具体类型由 PG 决定
        await db_session.flush()
    await db_session.rollback()


async def test_duplicate_edge_rejected_by_unique(db_session):
    """同向重复边被唯一约束拒（service 先查给 409，这里钉库级兜底）。"""
    owner = await _user(db_session)
    a = await _request(db_session, owner, "前置")
    b = await _request(db_session, owner, "后续")
    db_session.add(
        RequestDependency(request_id=b.id, dependency_id=a.id, created_by=owner.id)
    )
    await db_session.flush()
    db_session.add(
        RequestDependency(request_id=b.id, dependency_id=a.id, created_by=owner.id)
    )
    with pytest.raises(Exception):  # noqa: B017
        await db_session.flush()
    await db_session.rollback()


async def test_edges_cascade_with_request(db_session):
    """删需求 → 它作为被依赖方与作为前置的边都级联消失。"""
    owner = await _user(db_session)
    a = await _request(db_session, owner, "A")
    b = await _request(db_session, owner, "B")
    db_session.add(
        RequestDependency(request_id=b.id, dependency_id=a.id, created_by=owner.id)
    )
    await db_session.commit()

    await db_session.delete(a)
    await db_session.commit()

    # 直接查库绕身份映射（008 同款教训）。必须按本测试的 request_id 过滤——
    # 测试库是会话级共享的，别的文件经 API commit 的边也在这张表里
    # （不过滤会数到别人的数据，本特性第二轮就踩到）
    count = int(
        (
            await db_session.execute(
                text(
                    "SELECT count(*) FROM request_dependencies "
                    "WHERE request_id = :rid OR dependency_id = :rid"
                ),
                {"rid": b.id},
            )
        ).scalar()
    )
    assert count == 0
    # 被依赖方 b 本身还在
    assert await db_session.get(Request, b.id) is not None
