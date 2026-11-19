"""依赖服务的剩余纯函数分支（015 覆盖率补齐）。

`is_blocked` / `assert_not_blocked` / `user_exists` 都是「由列表/详情端点
派生的布尔/门禁」，正常路径测试通过端点间接覆盖了，但这些**边角分支**没有：

1. `assert_not_blocked` 的消息超过 5 个 blocker 时截断成「…等 N 个」——
   错误消息不是清单 UI，完整清单在详情弹层；截断逻辑本身不该被漏测。
2. `is_blocked` 的 True/False 两态（`unmet_dependencies` 非空则 blocked）。
3. `user_exists` 的正反两态。
"""

import uuid

import pytest

from app.models.request import Request
from app.models.user import User
from app.services import dependency_service
from app.services.dependency_service import DependencyBlockedError

pytestmark = pytest.mark.integration


async def _user(db, role="member") -> User:
    user = User(
        email=f"dep-{uuid.uuid4().hex[:8]}@test.com",
        password_hash="x" * 60,
        role=role,
        status="active",
    )
    db.add(user)
    await db.commit()
    return user


async def _request(db, owner: User, title: str, **kw) -> Request:
    req = Request(owner_id=owner.id, title=title, **kw)
    db.add(req)
    await db.commit()
    return req


async def _edge(db, dependent: Request, prerequisite: Request) -> None:
    from app.models.request_dependency import RequestDependency

    db.add(
        RequestDependency(
            request_id=dependent.id,
            dependency_id=prerequisite.id,
            created_by=dependent.owner_id,
        )
    )
    await db.commit()


async def test_is_blocked_true_when_unmet_dependency_exists(db_session):
    """有一条未完成前置即 blocked，不看前置数量。"""
    owner = await _user(db_session)
    dependent = await _request(db_session, owner, "dependent", status="created")
    prerequisite = await _request(db_session, owner, "prereq", status="running")
    await _edge(db_session, dependent, prerequisite)

    assert await dependency_service.is_blocked(db_session, dependent.id) is True


async def test_is_blocked_false_when_dependency_terminal(db_session):
    """前置终态（completed）即解锁：is_blocked 复用 unmet 判据。"""
    owner = await _user(db_session)
    dependent = await _request(db_session, owner, "dependent", status="created")
    prerequisite = await _request(db_session, owner, "prereq", status="completed")
    await _edge(db_session, dependent, prerequisite)

    assert await dependency_service.is_blocked(db_session, dependent.id) is False


async def test_assert_not_blocked_truncates_over_five_blockers(db_session):
    """超过 5 个 blocker 时消息截断，但仍报告总数。"""
    owner = await _user(db_session)
    dependent = await _request(db_session, owner, "dependent", status="created")
    for i in range(7):
        prereq = await _request(db_session, owner, f"prereq-{i}", status="running")
        await _edge(db_session, dependent, prereq)

    with pytest.raises(DependencyBlockedError) as exc:
        await dependency_service.assert_not_blocked(db_session, dependent.id)
    message = str(exc.value)
    assert "有 7 个" in message
    assert "等 7 个" in message  # 截断标记
    assert "prereq-6" not in message  # 第 6、7 个不在摘要里


async def test_user_exists_both_states(db_session):
    owner = await _user(db_session)
    assert await dependency_service.user_exists(db_session, owner.id) is True
    assert await dependency_service.user_exists(db_session, str(uuid.uuid4())) is False
