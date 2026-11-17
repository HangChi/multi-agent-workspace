"""配额集成测试（T077）：真实 DB 聚合 + 分配入口熔断（宪法「每个用户有月配额」）。

覆盖：
- get_month_usage：真实 SQL 聚合当前月 cost_cents（上月不计入）
- 配额熔断：月用量 >= 上限时，新分配返回 429 RATE_LIMITED（不建 Run、需求保持可分配）
- 未超限：正常分配 202
- 配额按用户隔离：A 用户超限不影响 B 用户
- 执行入口熔断：直接调用 quota_service.assert_quota_available 模拟 execute_run 前置检查
"""

import uuid
from datetime import UTC

import pytest

from app.models.request import Request
from app.models.run import Run
from app.services import quota_service

REQUESTS_URL = "/api/v1/requests"
AGENTS_URL = "/api/v1/agents"


async def _create_agent(client, headers: dict) -> dict:
    resp = await client.post(
        AGENTS_URL,
        headers=headers,
        json={
            "name": "quota-agent",
            "description": "quota test",
            "system_prompt": "you are helpful",
            "model": "gpt-4o-mini",
            "cost_limit_cents": 5000,
        },
    )
    assert resp.status_code == 201
    return resp.json()


async def _create_request(client, headers: dict, title: str) -> dict:
    resp = await client.post(
        REQUESTS_URL,
        headers=headers,
        json={"title": title, "description": "desc", "priority": "normal"},
    )
    assert resp.status_code == 201
    return resp.json()


async def _make_run(
    db_session, owner_id: str, cost_cents: int, *, status: str = "completed"
) -> Run:
    """直接建 Run（绕过分配，模拟已发生的执行成本）。"""
    request = Request(
        owner_id=owner_id, title=f"quota-{uuid.uuid4().hex[:6]}", status="completed"
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
    )
    db_session.add(run)
    await db_session.flush()
    return run


async def test_month_usage_sums_current_month_real_db(
    client, db_session, auth_headers, monkeypatch
):
    """get_month_usage：真实 SQL 聚合当月 cost_cents（上月不计入）。"""
    _, user = await auth_headers()

    # 本月两笔 + cancelled 一笔（不计）+ 上月一笔（不计）
    now_run = await _make_run(db_session, user.id, 120, status="completed")
    now_run2 = await _make_run(db_session, user.id, 80, status="completed")
    await _make_run(db_session, user.id, 999, status="cancelled")
    # 上月一笔
    prev = await _make_run(db_session, user.id, 500, status="completed")
    # 把 created_at 改到上月（SQL 层验证月份过滤）
    from datetime import datetime, timedelta

    prev.created_at = datetime.now(UTC) - timedelta(days=40)
    await db_session.flush()
    # 刷新：避免 ORM 缓存
    await db_session.commit()

    usage = await quota_service.get_month_usage(db_session, user.id)
    assert usage == now_run.cost_cents + now_run2.cost_cents  # 120 + 80 = 200


async def test_assign_rejected_when_quota_exceeded(
    client, db_session, auth_headers, monkeypatch
):
    """月用量超限：新分配 429 RATE_LIMITED，不建 Run，需求保持可分配。"""
    headers, user = await auth_headers()
    agent = await _create_agent(client, headers)

    # 预置本月已发生成本 = 配额上限（1_000_000 分 → 直接塞满）
    monkeypatch.setattr(quota_service, "MONTHLY_QUOTA_CENTS", 100)
    await _make_run(db_session, user.id, 100, status="completed")
    await db_session.commit()

    req = await _create_request(client, headers, "quota-blocked")
    resp = await client.post(
        f"{REQUESTS_URL}/{req['id']}/assign",
        headers=headers,
        json={"assignee_type": "agent", "assignee_id": agent["id"]},
    )
    assert resp.status_code == 429
    assert resp.json()["error"]["code"] == "RATE_LIMITED"

    # 不建 Run
    runs = (
        (
            await db_session.execute(
                __import__("sqlalchemy").select(Run).where(Run.request_id == req["id"])
            )
        )
        .scalars()
        .all()
    )
    assert runs == []
    # 需求保持可分配
    fresh = await db_session.get(Request, req["id"])
    assert fresh.status == "created"


async def test_assign_allowed_under_quota(
    client, db_session, auth_headers, monkeypatch
):
    """未超限：正常分配 202。"""
    headers, user = await auth_headers()
    agent = await _create_agent(client, headers)

    monkeypatch.setattr(quota_service, "MONTHLY_QUOTA_CENTS", 100)
    await _make_run(db_session, user.id, 50, status="completed")
    await db_session.commit()

    req = await _create_request(client, headers, "quota-ok")
    resp = await client.post(
        f"{REQUESTS_URL}/{req['id']}/assign",
        headers=headers,
        json={"assignee_type": "agent", "assignee_id": agent["id"]},
    )
    assert resp.status_code == 202
    assert resp.json()["status"] == "queued"


async def test_quota_isolated_per_user(client, db_session, auth_headers, monkeypatch):
    """配额按用户隔离：A 超限 429，B 同配额正常 202。"""
    headers_a, user_a = await auth_headers()
    headers_b, _ = await auth_headers()

    monkeypatch.setattr(quota_service, "MONTHLY_QUOTA_CENTS", 100)
    await _make_run(db_session, user_a.id, 100, status="completed")
    await db_session.commit()

    agent_b = await _create_agent(client, headers_b)
    req_b = await _create_request(client, headers_b, "quota-b-ok")
    resp_b = await client.post(
        f"{REQUESTS_URL}/{req_b['id']}/assign",
        headers=headers_b,
        json={"assignee_type": "agent", "assignee_id": agent_b["id"]},
    )
    assert resp_b.status_code == 202

    # A 用户再次分配 → 429
    agent_a = await _create_agent(client, headers_a)
    req_a = await _create_request(client, headers_a, "quota-a-blocked")
    resp_a = await client.post(
        f"{REQUESTS_URL}/{req_a['id']}/assign",
        headers=headers_a,
        json={"assignee_type": "agent", "assignee_id": agent_a["id"]},
    )
    assert resp_a.status_code == 429


async def test_execute_entry_asserts_quota(
    client, db_session, auth_headers, monkeypatch
):
    """执行入口熔断：直接调用 assert_quota_available 模拟 execute_run 前置检查。"""
    _, user = await auth_headers()
    monkeypatch.setattr(quota_service, "MONTHLY_QUOTA_CENTS", 100)
    await _make_run(db_session, user.id, 100, status="completed")
    await db_session.commit()

    usage = await quota_service.get_month_usage(db_session, user.id)
    assert usage == 100

    # 达到上限 → 熔断（执行入口拒绝新任务）
    with pytest.raises(quota_service.QuotaExceeded):
        quota_service.assert_quota_available(usage, quota_service.MONTHLY_QUOTA_CENTS)
