"""运行自动重试的集成测试（006 / T012-T019, T025-T027）。

这里锁的是**不能靠单测糊过去**的部分：真实数据库上的状态迁移、成本累计、
锁竞争、reaper 不误杀、以及 pipeline 不被重试态误判。

每个用例都跑完回滚（db_session 自带），但**提交过的数据不回收**——
与 005 的测试同一约定，因此断言一律用「相对量」而不是绝对计数。
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.core.events import subscribe_run_events
from app.executors.llm import LLMClientError
from app.models.agent import Agent
from app.models.request import Request
from app.models.run import Run
from app.models.user import User
from app.orchestrator.state_machine import RunStatus, transition
from app.services import reaper_service
from app.tasks.execute_run import _execute

pytestmark = pytest.mark.integration


# ---------- 辅助 ----------


async def _user(session) -> User:
    user = User(
        email=f"retry-{uuid.uuid4().hex[:8]}@test.com",
        password_hash="x" * 60,
        role="member",
        status="active",
    )
    session.add(user)
    await session.commit()
    return user


async def _agent(session, owner_id: str) -> Agent:
    agent = Agent(
        owner_id=owner_id,
        name="重试测试 Agent",
        system_prompt="p",
        model="m",
        temperature=0.7,
        max_steps=10,
        max_run_seconds=300,
        tools=[],
        egress_mode="default",
        cost_limit_cents=500,
    )
    session.add(agent)
    await session.commit()
    return agent


async def _request(session, *, owner_id: str) -> Request:
    req = Request(
        owner_id=owner_id,
        title="重试测试需求",
        description="desc",
        status="running",
        priority="normal",
    )
    session.add(req)
    await session.flush()
    return req


async def _run(session, *, owner_id: str, agent_id: str, **overrides) -> Run:
    """直接造一个 running Run（绕过分配链路，专注重试逻辑）。"""
    req = await _request(session, owner_id=owner_id)
    base = {
        "owner_id": owner_id,
        "request_id": req.id,
        "assignee_type": "agent",
        "assignee_id": agent_id,
        "strategy": "direct",
        "status": "running",
        "current_step": 0,
    }
    base.update(overrides)
    run = Run(**base)
    session.add(run)
    await session.commit()
    return run


def _failing_llm(monkeypatch, error: Exception):
    """让 LiteLLMClient.chat 立刻抛错（模拟网关故障）。"""

    async def chat(self, messages, **kwargs):
        raise error

    monkeypatch.setattr("app.tasks.execute_run.LiteLLMClient.chat", chat)


# ---------- T012：瞬时失败进入重试等待 ----------


@pytest.mark.asyncio
async def test_transient_failure_enters_retrying(db_session, monkeypatch, make_headers):
    """瞬时失败 → retrying + attempt/next_retry_at/retry_history 全部落库。"""
    owner = await _user(db_session)
    agent = await _agent(db_session, owner.id)
    run = await _run(db_session, owner_id=owner.id, agent_id=agent.id)

    _failing_llm(monkeypatch, LLMClientError("网关连接失败", transient=True))

    events: list[dict] = []
    stop = asyncio.Event()

    async def handler(payload: dict) -> None:
        events.append(payload)
        if payload.get("type") == "run_retrying":
            stop.set()

    # 先订阅再执行：订阅就绪与执行并发，否则会漏掉事件
    sub = asyncio.create_task(subscribe_run_events(str(run.id), handler))
    try:
        await _execute(db_session, run, agent)
        await db_session.refresh(run)
        await asyncio.wait_for(stop.wait(), timeout=3.0)
    except TimeoutError:
        pass
    finally:
        sub.cancel()

    assert run.status == RunStatus.RETRYING.value
    assert run.attempt == 1  # 首次执行，还没有重试过
    # full jitter 的下界是 0（research R-02），所以不能用「严格大于 now」断言，
    # 否则 jitter 取到接近 0 的值时会随机失败。断言区间更诚实：
    # 落在 [now, now + cap] 内即正确。
    now = datetime.now(UTC)
    assert run.next_retry_at is not None
    assert now - timedelta(seconds=1) <= run.next_retry_at
    assert run.next_retry_at <= now + timedelta(
        seconds=settings.run_retry_cap_seconds + 1
    )
    assert len(run.retry_history) == 1
    assert run.retry_history[0]["attempt"] == 1
    assert "网关连接失败" in run.retry_history[0]["error"]
    # 成本不因进入重试而清零（FR-013）
    # WS 推了 run_retrying（FR-019）
    assert any(e.get("type") == "run_retrying" for e in events)
    retrying_event = next(e for e in events if e.get("type") == "run_retrying")
    assert retrying_event["attempt"] == 2
    assert retrying_event["max_attempts"] == settings.run_max_attempts
    assert 0 <= retrying_event["wait_seconds"] <= settings.run_retry_cap_seconds


@pytest.mark.asyncio
async def test_auto_retry_writes_audit(db_session, monkeypatch):
    """自动重试调度写 run_auto_retry 审计（FR-018）。"""
    from app.models.audit_log import AuditLog

    owner = await _user(db_session)
    agent = await _agent(db_session, owner.id)
    run = await _run(db_session, owner_id=owner.id, agent_id=agent.id)

    _failing_llm(monkeypatch, LLMClientError("网关连接失败", transient=True))
    await _execute(db_session, run, agent)
    await db_session.flush()

    actions = set(
        (
            await db_session.execute(
                select(AuditLog.action).where(AuditLog.user_id == owner.id)
            )
        )
        .scalars()
        .all()
    )
    assert "run_auto_retry" in actions


# ---------- T014：业务类失败不重试 ----------


@pytest.mark.asyncio
async def test_permanent_failure_does_not_retry(db_session, monkeypatch, make_headers):
    """业务类失败（LLM 报错但非瞬时）→ 直接 failed，不投递重试。"""
    owner = await _user(db_session)
    agent = await _agent(db_session, owner.id)
    run = await _run(db_session, owner_id=owner.id, agent_id=agent.id)

    # 鉴权失败：LiteLLM 报同一个异常类，但 transient=False
    _failing_llm(monkeypatch, LLMClientError("LLM 调用失败: Invalid API key"))

    await _execute(db_session, run, agent)
    await db_session.refresh(run)

    assert run.status == RunStatus.FAILED.value
    assert run.attempt == 1
    assert run.next_retry_at is None
    # 失败原因仍然入历史（FR-017：每次尝试的败因都可查），只是没有安排重试
    assert len(run.retry_history) == 1
    assert "Invalid API key" in run.retry_history[0]["error"]


@pytest.mark.asyncio
async def test_unknown_failure_is_treated_as_permanent(
    db_session, monkeypatch, make_headers
):
    """未知异常（很可能是代码 bug）按业务类：不重试（research R-05）。"""
    owner = await _user(db_session)
    agent = await _agent(db_session, owner.id)
    run = await _run(db_session, owner_id=owner.id, agent_id=agent.id)

    _failing_llm(monkeypatch, AttributeError("'NoneType' has no attribute 'id'"))

    await _execute(db_session, run, agent)
    await db_session.refresh(run)

    assert run.status == RunStatus.FAILED.value
    assert run.attempt == 1
    assert run.next_retry_at is None
    # 注意：未知异常被 runner 的兜底分支吞成可读错误（test_runner.py 固化的行为），
    # 到不了 _execute 的重试判定，所以不入 retry_history——败因看 run.error 即可。
    # 关键断言是「没有安排重试」，不是「有没有历史」。
    assert "'NoneType'" in (run.error or "")


# ---------- T015：次数耗尽后终态 ----------


@pytest.mark.asyncio
async def test_exhausted_attempts_end_in_failed(db_session, monkeypatch, make_headers):
    """attempt 已达上限 → 终态 failed，不再投递重试（FR-009）。"""
    owner = await _user(db_session)
    agent = await _agent(db_session, owner.id)
    run = await _run(
        db_session,
        owner_id=owner.id,
        agent_id=agent.id,
        attempt=settings.run_max_attempts,  # 已用完
        status="running",
    )

    _failing_llm(monkeypatch, LLMClientError("网关连接失败", transient=True))

    await _execute(db_session, run, agent)
    await db_session.refresh(run)

    assert run.status == RunStatus.FAILED.value
    assert run.attempt == settings.run_max_attempts
    assert run.next_retry_at is None
    # 历史保留：用户能看到它试过几次、每次败在哪（FR-017）
    assert len(run.retry_history) >= 1


# ---------- T013：重试到点后成功，成本累计 ----------


@pytest.mark.asyncio
async def test_retry_success_accumulates_cost(db_session, monkeypatch, make_headers):
    """第 1 次花了 30 分失败，第 2 次花 25 分成功 → 合计 55 分（SC-003）。"""
    from app.executors.llm import LLMResponse

    owner = await _user(db_session)
    agent = await _agent(db_session, owner.id)
    run = await _run(
        db_session,
        owner_id=owner.id,
        agent_id=agent.id,
        cost_micros=30_000_000,  # 第一次尝试已花 30 分
        cost_cents=30,
        attempt=1,
        status="running",
    )

    # 第一次：瞬时失败
    _failing_llm(monkeypatch, LLMClientError("网关连接失败", transient=True))
    await _execute(db_session, run, agent)
    await db_session.refresh(run)
    assert run.status == RunStatus.RETRYING.value
    assert run.cost_micros == 30_000_000  # 不清零

    # 第二次：成功，花 25 分
    async def ok_chat(self, messages, **kwargs):
        return LLMResponse(content="成功了", cost_cents=25)

    monkeypatch.setattr("app.tasks.execute_run.LiteLLMClient.chat", ok_chat)
    # 模拟重试到点：_main 里会把 retrying → running 并递增 attempt，
    # 这里直接驱动 _execute，所以手动复现这两步
    run.bump_attempt()
    run.next_retry_at = None
    transition(run, RunStatus.RUNNING)
    await db_session.commit()

    await _execute(db_session, run, agent)
    await db_session.refresh(run)

    assert run.status == RunStatus.COMPLETED.value
    assert run.cost_micros == 55_000_000
    assert run.cost_cents == 55
    assert run.attempt == 2  # 重试时递增


# ---------- T018：累计成本达到上限被熔断 ----------


@pytest.mark.asyncio
async def test_accumulated_cost_trips_the_limit(db_session, monkeypatch, make_headers):
    """累计已达上限 → 重试尝试继续执行时被熔断，不超支（FR-014）。"""
    from app.executors.llm import LLMResponse

    owner = await _user(db_session)
    agent = await _agent(db_session, owner.id)
    agent.cost_limit_cents = 50
    await db_session.commit()
    run = await _run(
        db_session,
        owner_id=owner.id,
        agent_id=agent.id,
        cost_micros=49_000_000,  # 已花 49/50
        cost_cents=49,
        attempt=2,
        status="running",
    )

    # 这一步会花 25 分，超过剩余的 1 分 → 应被熔断
    async def costly_chat(self, messages, **kwargs):
        return LLMResponse(content="x", cost_cents=25)

    monkeypatch.setattr("app.tasks.execute_run.LiteLLMClient.chat", costly_chat)

    await _execute(db_session, run, agent)
    await db_session.refresh(run)

    assert run.status == RunStatus.FAILED.value
    assert "成本超限" in (run.error or "")
    # 关键：起点是**累计的 49 分**，所以这一步就直接触发熔断。
    # 若重试时重置成本（006 之前的行為），起点会是 0，这一步只到 25 分，
    # 上限 50 分永远不会触发——那正是 FR-014 要堵的洞。
    assert run.cost_micros == 74_000_000
    # 熔断是业务类失败：不安排重试
    assert run.next_retry_at is None


# ---------- T016：锁竞争不重复执行 ----------


@pytest.mark.asyncio
async def test_lock_contention_prevents_double_execution(
    db_session, monkeypatch, make_headers
):
    """锁被占用时再次触发 → busy，不产生第二次执行。"""
    from unittest.mock import patch

    from app.tasks import execute_run as task_module

    owner = await _user(db_session)
    agent = await _agent(db_session, owner.id)
    run = await _run(db_session, owner_id=owner.id, agent_id=agent.id)

    executed = []

    async def counting_chat(self, messages, **kwargs):
        from app.executors.llm import LLMResponse

        executed.append(1)
        return LLMResponse(content="done", cost_cents=1)

    monkeypatch.setattr("app.tasks.execute_run.LiteLLMClient.chat", counting_chat)

    # 预占锁：模拟另一个 Worker 正在执行同一个 Run
    # 注意：execute_run 在 TESTING=1 时会短路返回 skipped-test，
    # 这里显式关掉该短路，直接验锁竞争（生产上「两个执行体撞锁」的路径）。
    monkeypatch.delenv("TESTING", raising=False)
    with patch.object(task_module, "_acquire_lock", return_value=False):
        result = await asyncio.to_thread(task_module.execute_run.run, str(run.id))
    assert result == "busy"
    assert executed == []  # 一次都没执行

    # 释放锁后正常执行一次
    with patch.object(task_module, "_acquire_lock", return_value=True):
        result = await asyncio.to_thread(task_module.execute_run.run, str(run.id))
    assert result == "completed"
    assert len(executed) == 1


# ---------- T017：终态后延迟消息被丢弃 ----------


@pytest.mark.asyncio
async def test_delayed_retry_message_discarded_after_terminal(
    db_session, monkeypatch, make_headers
):
    """Run 已 completed，延迟的重试消息到点 → 丢弃，状态与成本不变（FR-011）。"""
    from unittest.mock import patch

    from app.tasks import execute_run as task_module

    owner = await _user(db_session)
    agent = await _agent(db_session, owner.id)
    run = await _run(
        db_session,
        owner_id=owner.id,
        agent_id=agent.id,
        status="completed",
        cost_cents=10,
        cost_micros=10_000_000,
        next_retry_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    await db_session.commit()

    executed = []

    async def counting_chat(self, messages, **kwargs):
        from app.executors.llm import LLMResponse

        executed.append(1)
        return LLMResponse(content="should not happen", cost_cents=99)

    monkeypatch.setattr("app.tasks.execute_run.LiteLLMClient.chat", counting_chat)

    monkeypatch.delenv("TESTING", raising=False)
    with patch.object(task_module, "_acquire_lock", return_value=True):
        result = await asyncio.to_thread(task_module.execute_run.run, str(run.id))

    assert executed == []  # 没有执行
    assert result == "discarded-completed"
    await db_session.refresh(run)
    assert run.status == RunStatus.COMPLETED.value
    assert run.cost_micros == 10_000_000


# ---------- T025：reaper 不误杀重试中的 Run ----------


@pytest.mark.asyncio
async def test_reaper_does_not_reap_retrying_runs(
    db_session, monkeypatch, make_headers
):
    """status=retrying 且 updated_at 超阈值 → reaper 不得回收（FR-012）。"""
    owner = await _user(db_session)
    agent = await _agent(db_session, owner.id)
    run = await _run(
        db_session,
        owner_id=owner.id,
        agent_id=agent.id,
        status="retrying",
        attempt=1,
        next_retry_at=datetime.now(UTC) + timedelta(seconds=30),
    )
    # 把 updated_at 推到很久以前（若按时间捞僵尸，它会被选中）
    await db_session.execute(
        Run.__table__.update()
        .where(Run.id == run.id)
        .values(updated_at=datetime.now(UTC) - timedelta(hours=2))
    )
    await db_session.commit()

    swept = await reaper_service.reap_stale_runs(db_session)
    assert run.id not in {r.id for r in swept}

    await db_session.refresh(run)
    assert run.status == RunStatus.RETRYING.value


# ---------- T026/T027：查询面 ----------


@pytest.mark.asyncio
async def test_run_detail_exposes_retry_fields(
    client, db_session, make_user, make_headers
):
    """GET /runs/{id} 返回 attempt / next_retry_at / retry_history（FR-015）。"""
    owner, _ = await make_user()
    headers = await make_headers(user=owner)
    agent = await _agent(db_session, owner.id)
    run = await _run(
        db_session,
        owner_id=owner.id,
        agent_id=agent.id,
        status="retrying",
        attempt=2,
        next_retry_at=datetime.now(UTC) + timedelta(seconds=4),
        retry_history=[
            {"attempt": 1, "error": "网关抖动", "at": "2026-11-18T09:00:00+00:00"}
        ],
    )
    await db_session.commit()

    resp = await client.get(f"/api/v1/runs/{run.id}", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["attempt"] == 2
    assert body["next_retry_at"] is not None
    assert len(body["retry_history"]) == 1
    assert body["retry_history"][0]["error"] == "网关抖动"


@pytest.mark.asyncio
async def test_legacy_run_gets_default_retry_fields(
    client, db_session, make_user, make_headers
):
    """迁移前创建的 Run（三个字段走默认值）不会因迁移变成 null。"""
    owner, _ = await make_user()
    headers = await make_headers(user=owner)
    agent = await _agent(db_session, owner.id)
    run = await _run(
        db_session, owner_id=owner.id, agent_id=agent.id, status="completed"
    )
    await db_session.commit()

    resp = await client.get(f"/api/v1/runs/{run.id}", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["attempt"] == 1
    assert body["next_retry_at"] is None
    assert body["retry_history"] == []
