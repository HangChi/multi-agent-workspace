"""统计端点集成测试（T078）：GET /stats/overview 仪表盘数据（对齐 tasks.md T078）。

覆盖：
- 未认证 401
- member/viewer 可访问（自己的统计）
- running/queued 计数
- completed_today / failed_today（今日终态）
- success_rate_7d（近 7 日成功率 = completed / (completed+failed)）
- cost_today_cents / cost_trend_7d（近 7 日成本序列）
- 数据按用户隔离：A 的 Run 不计入 B 统计
- 空数据：全 0 / 空序列（不报错）
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.models.request import Request
from app.models.run import Run

STATS_URL = "/api/v1/stats/overview"


async def _make_run(
    db_session,
    owner_id: str,
    cost_cents: int,
    *,
    status: str = "completed",
    created_at: datetime | None = None,
    finished_at: datetime | None = None,
) -> Run:
    request = Request(
        owner_id=owner_id, title=f"stats-{uuid.uuid4().hex[:6]}", status="completed"
    )
    db_session.add(request)
    await db_session.flush()
    run = Run(
        request_id=request.id,
        owner_id=owner_id,
        assignee_type="agent",
        assignee_id=str(uuid.uuid4()),
        strategy="direct",
        status=status,
        cost_cents=cost_cents,
        created_at=created_at or datetime.now(UTC),
    )
    if finished_at:
        run.finished_at = finished_at
    db_session.add(run)
    await db_session.flush()
    return run


async def test_stats_requires_auth(client):
    """未认证 → 401。"""
    resp = await client.get(STATS_URL)
    assert resp.status_code == 401


async def test_stats_empty_defaults(client, auth_headers):
    """空数据：全 0 / 空序列。"""
    headers, _ = await auth_headers()
    resp = await client.get(STATS_URL, headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["running"] == 0
    assert data["queued"] == 0
    assert data["completed_today"] == 0
    assert data["failed_today"] == 0
    assert data["success_rate_7d"] == 0.0
    assert data["cost_today_cents"] == 0
    # 近 7 日序列：固定 7 个元素，空数据全 0
    assert data["cost_trend_7d"] == [0, 0, 0, 0, 0, 0, 0]


async def test_stats_counts_runs(client, db_session, auth_headers):
    """running/queued/completed_today/failed_today 计数。"""
    headers, user = await auth_headers()
    now = datetime.now(UTC)

    await _make_run(db_session, user.id, 100, status="running", created_at=now)
    await _make_run(db_session, user.id, 50, status="queued", created_at=now)
    await _make_run(db_session, user.id, 200, status="completed", created_at=now)
    await _make_run(db_session, user.id, 30, status="failed", created_at=now)
    await db_session.commit()

    resp = await client.get(STATS_URL, headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["running"] == 1
    assert data["queued"] == 1
    assert data["completed_today"] == 1
    assert data["failed_today"] == 1


async def test_stats_success_rate_7d(client, db_session, auth_headers):
    """近 7 日成功率 = completed / (completed+failed)。"""
    headers, user = await auth_headers()
    now = datetime.now(UTC)

    await _make_run(db_session, user.id, 100, status="completed", created_at=now)
    await _make_run(db_session, user.id, 100, status="completed", created_at=now)
    await _make_run(db_session, user.id, 100, status="failed", created_at=now)
    # 7 日前（不计入 7d 窗口）
    old = now - timedelta(days=8)
    await _make_run(db_session, user.id, 100, status="failed", created_at=old)
    await db_session.commit()

    resp = await client.get(STATS_URL, headers=headers)
    data = resp.json()
    assert data["success_rate_7d"] == pytest.approx(
        2 / 3, abs=1e-4
    )  # 2 completed / 3 total


async def test_stats_cost_today_and_trend(client, db_session, auth_headers):
    """cost_today_cents 与 cost_trend_7d（近 7 日成本序列）。"""
    headers, user = await auth_headers()
    now = datetime.now(UTC)

    await _make_run(db_session, user.id, 100, status="completed", created_at=now)
    await _make_run(db_session, user.id, 50, status="failed", created_at=now)
    # 6 天前（计入 7d 窗口，不计今日）
    six_days_ago = now - timedelta(days=6)
    await _make_run(
        db_session, user.id, 20, status="completed", created_at=six_days_ago
    )
    await db_session.commit()

    resp = await client.get(STATS_URL, headers=headers)
    data = resp.json()
    assert data["cost_today_cents"] == 150  # 100 + 50
    assert len(data["cost_trend_7d"]) == 7  # 近 7 日每天一个值
    assert sum(data["cost_trend_7d"]) == 170  # 150 + 20


async def test_stats_isolated_per_user(client, db_session, auth_headers):
    """统计按用户隔离：A 的 Run 不计入 B。"""
    headers_a, user_a = await auth_headers()
    headers_b, _ = await auth_headers()
    now = datetime.now(UTC)

    await _make_run(db_session, user_a.id, 100, status="completed", created_at=now)
    await _make_run(db_session, user_a.id, 100, status="completed", created_at=now)
    await db_session.commit()

    resp_a = await client.get(STATS_URL, headers=headers_a)
    data_a = resp_a.json()
    assert data_a["completed_today"] == 2
    assert data_a["cost_today_cents"] == 200

    resp_b = await client.get(STATS_URL, headers=headers_b)
    data_b = resp_b.json()
    assert data_b["completed_today"] == 0
    assert data_b["cost_today_cents"] == 0


async def test_active_counts_ignore_time_window(client, db_session, auth_headers):
    """非终态计数不受 7 天窗口限制（跑了很久的任务不能从仪表盘消失）。"""
    headers, user = await auth_headers()
    old = datetime.now(UTC) - timedelta(days=20)

    await _make_run(db_session, user.id, 0, status="running", created_at=old)
    await _make_run(db_session, user.id, 0, status="queued", created_at=old)
    await db_session.commit()

    resp = await client.get(STATS_URL, headers=headers)
    data = resp.json()
    assert data["running"] == 1
    assert data["queued"] == 1
    # 成本趋势仍只统计近 7 天，20 天前的任务不进入趋势
    assert sum(data["cost_trend_7d"]) == 0
