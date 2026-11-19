"""出站访问记录落库（015 覆盖率补齐）。

`record_accesses` 是沙箱执行后把连接事实写进库的唯一入口。它的输入来自
netns 里抓到的 conntrack/DNS 记录，字段质量不可控（时间可能缺失/畸形、
IP 可能是空串或非法值）。这些脏数据不能把整条执行写失败——否则一次 egress
审计异常会连带把整个 Run 的执行结果回滚掉。

因此这里逐条钉住三类「脏输入」的处置：
1. 非法/空 IP：跳过该条（不是抛异常）；
2. `occurred_at` 缺省或字符串非法：回退到 now(UTC)；
3. domain 缺省：回退到 "ip-direct"（纯 IP 直连，没有域名）。
"""

import uuid
from datetime import UTC, datetime

import pytest

from app.models.agent import Agent
from app.models.request import Request
from app.models.run import Run
from app.models.user import User
from app.services import egress_service

pytestmark = pytest.mark.integration


async def _fixture(db):
    """构造 User + Agent + Request + Run，满足 egress_accesses 的外键。"""
    user = User(
        email=f"eg-{uuid.uuid4().hex[:8]}@test.com",
        password_hash="x" * 60,
        role="member",
        status="active",
    )
    db.add(user)
    await db.flush()
    agent = Agent(
        owner_id=user.id,
        name=f"agent-{uuid.uuid4().hex[:6]}",
        model="openai/gpt-4o-mini",
        system_prompt="x",
        tools=[],
    )
    db.add(agent)
    await db.flush()
    request = Request(owner_id=user.id, title="egress", status="running")
    db.add(request)
    await db.flush()
    run = Run(
        request_id=request.id,
        owner_id=user.id,
        assignee_type="agent",
        assignee_id=agent.id,
        strategy="direct",
        status="running",
    )
    db.add(run)
    await db.commit()
    return user, agent, run


async def test_record_accesses_skips_invalid_ips(db_session):
    """非法 IP 静默跳过：脏数据不能把整条执行写失败。"""
    _user, agent, run = await _fixture(db_session)
    rows = await egress_service.record_accesses(
        db_session,
        run_id=run.id,
        agent_id=agent.id,
        user_id=_user.id,
        accesses=[
            {"target_ip": "93.184.216.34"},
            {"target_ip": "not-an-ip"},
            {"target_ip": ""},
            {"target_ip": "999.1.1.1"},
        ],
    )
    assert len(rows) == 1
    assert rows[0].target_ip == "93.184.216.34"


async def test_record_accesses_defaults_domain_and_time(db_session):
    """domain 缺省回退 ip-direct，时间缺省回退 now(UTC)。"""
    _user, agent, run = await _fixture(db_session)
    before = datetime.now(UTC)
    rows = await egress_service.record_accesses(
        db_session,
        run_id=run.id,
        agent_id=agent.id,
        user_id=_user.id,
        accesses=[{"target_ip": "93.184.216.34", "port": 443}],
    )
    row = rows[0]
    assert row.target_domain == "ip-direct"
    assert row.port == 443
    assert row.protocol == "tcp"
    assert row.action == "allow"
    assert before <= row.occurred_at <= datetime.now(UTC)


async def test_record_accesses_parses_iso_time_and_prefers_domain_key(db_session):
    """合法 ISO 时间直接用；domain 优先取 target_domain 再退 domain 键。"""
    _user, agent, run = await _fixture(db_session)
    stamp = "2026-11-19T10:00:00+00:00"
    rows = await egress_service.record_accesses(
        db_session,
        run_id=run.id,
        agent_id=agent.id,
        user_id=_user.id,
        accesses=[
            {
                "target_ip": "93.184.216.34",
                "target_domain": "example.com",
                "occurred_at": stamp,
                "action": "deny",
            }
        ],
    )
    row = rows[0]
    assert row.target_domain == "example.com"
    assert row.occurred_at == datetime.fromisoformat(stamp)
    assert row.action == "deny"


async def test_record_accesses_falls_back_to_now_on_bad_time(db_session):
    """字符串时间非法时回退 now(UTC)，而不是让 fromisoformat 冒出去。"""
    _user, agent, run = await _fixture(db_session)
    rows = await egress_service.record_accesses(
        db_session,
        run_id=run.id,
        agent_id=agent.id,
        user_id=_user.id,
        accesses=[
            {"target_ip": "93.184.216.34", "occurred_at": "not-a-date"},
        ],
    )
    assert len(rows) == 1
    assert rows[0].occurred_at is not None


async def test_record_accesses_domain_falls_back_to_domain_key(db_session):
    """没有 target_domain 但有 domain 键时用后者。"""
    _user, agent, run = await _fixture(db_session)
    rows = await egress_service.record_accesses(
        db_session,
        run_id=run.id,
        agent_id=agent.id,
        user_id=_user.id,
        accesses=[
            {"target_ip": "93.184.216.34", "domain": "legacy.example.com"},
        ],
    )
    assert rows[0].target_domain == "legacy.example.com"
