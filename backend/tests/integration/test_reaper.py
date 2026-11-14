"""僵尸 Run 回收测试：Worker 崩溃/未消费后看板不再永久卡「执行中」。

覆盖：
- queued 超时（Worker 未消费）→ failed，可重试
- running 超时（Worker 被杀）→ failed
- 新鲜 Run 不受影响（不误杀正常排队的任务）
- 关联需求从 running 回收为 failed（无其他活跃 Run 时）
- 需求还有其他活跃 Run 时不动（fan_out 部分完成场景）
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.core.security import hash_password
from app.models.request import Request
from app.models.run import Run
from app.models.user import User
from app.services.reaper_service import reap_stale_runs

NOW = datetime.now(UTC)


async def _user(db) -> User:
    user = User(
        email=f"reap-{uuid.uuid4().hex[:10]}@test.com",
        password_hash=hash_password("TestPass123!"),
        role="member",
        status="active",
    )
    db.add(user)
    await db.flush()
    return user


async def _request(db, user: User, *, status: str = "running") -> Request:
    req = Request(
        owner_id=user.id,
        title=f"reap-{uuid.uuid4().hex[:6]}",
        priority="normal",
        status=status,
    )
    db.add(req)
    await db.flush()
    return req


async def _run(
    db,
    req: Request,
    user: User,
    *,
    status: str,
    age_minutes: int,
    started: bool = False,
) -> Run:
    created = NOW - timedelta(minutes=age_minutes)
    run = Run(
        request_id=req.id,
        owner_id=user.id,
        assignee_type="agent",
        assignee_id=str(uuid.uuid4()),
        strategy="direct",
        status=status,
        created_at=created,
        started_at=created if started else None,
    )
    db.add(run)
    await db.flush()
    return run


async def test_reaps_stale_queued_run(db_session):
    """queued 超时 → failed，错误信息可读，并回落到可重试终态。"""
    user = await _user(db_session)
    req = await _request(db_session, user)
    stale = await _run(db_session, req, user, status="queued", age_minutes=120)

    reaped = await reap_stale_runs(db_session)
    await db_session.commit()

    assert stale.id in reaped
    await db_session.refresh(stale)
    assert stale.status == "failed"
    assert "排队超时" in (stale.error or "")


async def test_reaps_stale_running_run(db_session):
    """running 超时（Worker 被杀）→ failed。"""
    user = await _user(db_session)
    req = await _request(db_session, user)
    stale = await _run(
        db_session, req, user, status="running", age_minutes=300, started=True
    )

    reaped = await reap_stale_runs(db_session)
    await db_session.commit()

    assert stale.id in reaped
    await db_session.refresh(stale)
    assert stale.status == "failed"
    assert "执行超时" in (stale.error or "")


async def test_does_not_touch_fresh_runs(db_session):
    """新鲜 queued/running 不受影响（不误杀正常任务）。"""
    user = await _user(db_session)
    req = await _request(db_session, user)
    fresh_queued = await _run(db_session, req, user, status="queued", age_minutes=1)
    fresh_running = await _run(
        db_session, req, user, status="running", age_minutes=1, started=True
    )

    reaped = await reap_stale_runs(db_session)
    await db_session.commit()

    assert reaped == []
    await db_session.refresh(fresh_queued)
    await db_session.refresh(fresh_running)
    assert fresh_queued.status == "queued"
    assert fresh_running.status == "running"


async def test_recovers_request_status(db_session):
    """回收后无活跃 Run → 需求从 running 回落 failed（看板不再卡执行中）。"""
    user = await _user(db_session)
    req = await _request(db_session, user, status="running")
    await _run(db_session, req, user, status="queued", age_minutes=120)

    await reap_stale_runs(db_session)
    await db_session.commit()

    await db_session.refresh(req)
    assert req.status == "failed"


async def test_keeps_request_when_other_runs_active(db_session):
    """fan_out 场景：仍有子 Run 在跑时，需求不被回收。"""
    user = await _user(db_session)
    req = await _request(db_session, user, status="running")
    await _run(db_session, req, user, status="queued", age_minutes=120)
    await _run(db_session, req, user, status="running", age_minutes=1, started=True)

    await reap_stale_runs(db_session)
    await db_session.commit()

    await db_session.refresh(req)
    assert req.status == "running"


@pytest.mark.parametrize("status", ["completed", "cancelled", "failed"])
async def test_ignores_terminal_runs(db_session, status: str):
    """终态 Run 永不被回收（幂等，不覆盖已有结果）。"""
    user = await _user(db_session)
    req = await _request(db_session, user)
    old = await _run(db_session, req, user, status=status, age_minutes=500, started=True)

    reaped = await reap_stale_runs(db_session)
    await db_session.commit()

    assert old.id not in reaped
    await db_session.refresh(old)
    assert old.status == status
