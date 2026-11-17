"""需求状态收敛：需求必须跟着它名下的 Run 一起走到终态。

分配把需求置为 running 之后就没人再动它，看板的「已完成 / 失败」两列永远是空的，
卡片一直停在「执行中」。这里钉住收敛规则：还有未终态的 Run 就维持 running，
否则 失败 > 取消 > 完成。
"""

from __future__ import annotations

import uuid

from app.models.request import Request
from app.models.run import Run
from app.models.user import User
from app.services.request_service import settle_request_status


async def _request_with_runs(
    db_session, statuses: list[str], request_status: str = "running"
) -> Request:
    owner = User(
        email=f"settle-{uuid.uuid4().hex[:8]}@test.com",
        password_hash="x",
        role="member",
    )
    db_session.add(owner)
    await db_session.flush()
    request = Request(owner_id=owner.id, title="settle", status=request_status)
    db_session.add(request)
    await db_session.flush()
    for status in statuses:
        db_session.add(
            Run(
                request_id=request.id,
                owner_id=owner.id,
                assignee_type="agent",
                assignee_id=owner.id,
                strategy="direct",
                sequence=0,
                status=status,
            )
        )
    await db_session.commit()
    await db_session.refresh(request)
    return request


async def test_all_runs_completed_settles_the_request(db_session):
    request = await _request_with_runs(db_session, ["completed", "completed"])
    assert await settle_request_status(db_session, request.id) == "completed"
    await db_session.commit()  # 收敛只改内存，提交由调用方负责
    await db_session.refresh(request)
    assert request.status == "completed"


async def test_any_active_run_keeps_the_request_running(db_session):
    request = await _request_with_runs(db_session, ["completed", "running"])
    assert await settle_request_status(db_session, request.id) is None
    await db_session.commit()
    await db_session.refresh(request)
    assert request.status == "running"


async def test_a_failure_wins_over_completion(db_session):
    request = await _request_with_runs(db_session, ["completed", "failed"])
    assert await settle_request_status(db_session, request.id) == "failed"


async def test_a_timeout_also_counts_as_failure(db_session):
    request = await _request_with_runs(db_session, ["completed", "timeout"])
    assert await settle_request_status(db_session, request.id) == "failed"


async def test_cancellation_wins_over_completion_but_loses_to_failure(db_session):
    cancelled = await _request_with_runs(db_session, ["completed", "cancelled"])
    assert await settle_request_status(db_session, cancelled.id) == "cancelled"

    failed_and_cancelled = await _request_with_runs(db_session, ["cancelled", "failed"])
    assert await settle_request_status(db_session, failed_and_cancelled.id) == "failed"


async def test_a_settled_or_unassigned_request_is_never_rewritten(db_session):
    """只收敛 running 的需求：reaper 的判定和未分配的卡片都不能被覆盖。"""
    for status in ("completed", "failed", "cancelled", "created", "assigned"):
        request = await _request_with_runs(
            db_session, ["failed"], request_status=status
        )
        assert await settle_request_status(db_session, request.id) is None
        await db_session.commit()
        await db_session.refresh(request)
        assert request.status == status
