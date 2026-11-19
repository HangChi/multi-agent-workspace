"""execute_run 的流水线辅助函数单测（015 覆盖率补齐）。

这些函数是 pipeline 策略的中枢：
- `_advance_pipeline` 决定「下一阶段何时触发」「失败时谁被取消」「父 Run 何时收敛」；
- `_finalize_parent` 负责把父 Run 落成 completed/failed 并推事件。

它们的失败模式都很安静（父 Run 卡在 queued、剩余阶段继续排队烧钱），所以逐条
锁住分支比端到端跑一遍更划算。事件发布统一 monkeypatch 掉——这里验的是
「有没有发、发的是什么状态」，不是 WebSocket/Redis 本身。
"""

import uuid

import pytest

from app.models.agent import Agent
from app.models.request import Request
from app.models.run import Run
from app.models.user import User
from app.tasks import execute_run as er

pytestmark = pytest.mark.integration


@pytest.fixture
def events(monkeypatch):
    """收集事件推送调用（不真的发 Redis）。"""
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(
        er.run_events, "run_failed", lambda rid, err: sent.append(("failed", rid))
    )
    monkeypatch.setattr(
        er.run_events,
        "run_completed",
        lambda rid, result: sent.append(("completed", rid)),
    )
    monkeypatch.setattr(
        er.run_events, "log_line", lambda rid, line: sent.append(("log", line))
    )
    return sent


async def _fixture(db, *, status="completed", sequence=1):
    """造 用户 + 需求 + 父 Run + 当前阶段 Run。"""
    user = User(
        email=f"er-{uuid.uuid4().hex[:8]}@test.com",
        password_hash="x" * 60,
        role="member",
        status="active",
    )
    db.add(user)
    await db.flush()
    agent = Agent(
        owner_id=user.id,
        name=f"er-agent-{uuid.uuid4().hex[:6]}",
        model="openai/gpt-4o-mini",
        system_prompt="x",
        tools=[],
    )
    db.add(agent)
    await db.flush()
    request = Request(owner_id=user.id, title="依赖需求", status="running")
    db.add(request)
    await db.flush()
    parent = Run(
        request_id=request.id,
        owner_id=user.id,
        assignee_type="group",
        assignee_id=str(uuid.uuid4()),
        strategy="pipeline",
        status="queued",
    )
    db.add(parent)
    await db.flush()
    stage = Run(
        request_id=request.id,
        parent_run_id=parent.id,
        owner_id=user.id,
        assignee_type="agent",
        assignee_id=agent.id,
        strategy="pipeline",
        sequence=sequence,
        status=status,
    )
    db.add(stage)
    await db.commit()
    await db.refresh(parent)
    await db.refresh(stage)
    return request, parent, stage, agent


# ---------- _previous_stage_output ----------


async def test_previous_stage_output_reads_prev_answer(db_session):
    _request, parent, stage, _agent = await _fixture(db_session, sequence=2)
    prev = Run(
        request_id=stage.request_id,
        parent_run_id=parent.id,
        owner_id=parent.owner_id,
        assignee_type="agent",
        assignee_id=stage.assignee_id,
        strategy="pipeline",
        sequence=1,
        status="completed",
        result={"answer": "上一阶段产出"},
    )
    db_session.add(prev)
    await db_session.commit()

    assert await er._previous_stage_output(db_session, stage) == "上一阶段产出"


async def test_previous_stage_output_none_when_missing_or_blank(db_session):
    """没有上一阶段 / result 不是 dict / answer 空白，三种都返回 None。"""
    _request, parent, stage, _agent = await _fixture(db_session, sequence=5)
    assert await er._previous_stage_output(db_session, stage) is None

    prev = Run(
        request_id=stage.request_id,
        parent_run_id=parent.id,
        owner_id=parent.owner_id,
        assignee_type="agent",
        assignee_id=stage.assignee_id,
        strategy="pipeline",
        sequence=4,
        status="completed",
        result={"answer": "   "},
    )
    db_session.add(prev)
    await db_session.commit()
    assert await er._previous_stage_output(db_session, stage) is None


# ---------- _finalize_parent ----------


async def test_finalize_parent_success_lists_stages_sorted(db_session, events):
    _request, parent, stage, _agent = await _fixture(db_session)
    second = Run(
        request_id=stage.request_id,
        parent_run_id=parent.id,
        owner_id=parent.owner_id,
        assignee_type="agent",
        assignee_id=stage.assignee_id,
        strategy="pipeline",
        sequence=2,
        status="completed",
        cost_cents=7,
    )
    db_session.add(second)
    await db_session.commit()

    await er._finalize_parent(
        db_session, parent.id, failed=False, error=None, stages=[second, stage]
    )

    refreshed = await db_session.get(Run, parent.id)
    assert refreshed.status == "completed"
    assert [s["sequence"] for s in refreshed.result["stages"]] == [1, 2]
    assert ("completed", parent.id) in events


async def test_finalize_parent_failure_records_reason(db_session, events):
    _request, parent, stage, _agent = await _fixture(db_session)

    await er._finalize_parent(
        db_session, parent.id, failed=True, error="boom", stages=[stage]
    )

    refreshed = await db_session.get(Run, parent.id)
    assert refreshed.status == "failed"
    assert "boom" in refreshed.error
    assert ("failed", parent.id) in events


async def test_finalize_parent_noop_when_not_queued(db_session, events):
    """幂等：父 Run 已经是终态时再收敛一次不应改写结果。"""
    _request, parent, stage, _agent = await _fixture(db_session)
    parent.status = "cancelled"
    await db_session.commit()

    await er._finalize_parent(
        db_session, parent.id, failed=True, error="x", stages=[stage]
    )

    refreshed = await db_session.get(Run, parent.id)
    assert refreshed.status == "cancelled"
    assert events == []


# ---------- _advance_pipeline ----------


async def test_advance_pipeline_skips_needs_input(db_session, events):
    """挂起等回复的阶段不推进：此时推进会把链路误判成失败。"""
    _request, _parent, stage, _agent = await _fixture(db_session, status="needs_input")

    await er._advance_pipeline(db_session, stage)

    assert [e for e in events if e[0] in {"failed", "completed"}] == []


async def test_advance_pipeline_enqueues_next_stage(db_session, events, monkeypatch):
    _request, parent, stage, _agent = await _fixture(
        db_session, status="completed", sequence=1
    )
    next_stage = Run(
        request_id=stage.request_id,
        parent_run_id=parent.id,
        owner_id=parent.owner_id,
        assignee_type="agent",
        assignee_id=stage.assignee_id,
        strategy="pipeline",
        sequence=2,
        status="queued",
    )
    db_session.add(next_stage)
    await db_session.commit()

    enqueued: list[list[str]] = []
    monkeypatch.setattr(er, "_enqueue", lambda ids: enqueued.append(list(ids)))

    await er._advance_pipeline(db_session, stage)

    assert enqueued == [[next_stage.id]]


async def test_advance_pipeline_failure_cancels_remaining(db_session, events):
    """中间阶段失败 → 剩余阶段取消 + 父 Run 收敛失败（避免继续烧钱）。"""
    _request, parent, stage, _agent = await _fixture(
        db_session, status="failed", sequence=1
    )
    later = Run(
        request_id=stage.request_id,
        parent_run_id=parent.id,
        owner_id=parent.owner_id,
        assignee_type="agent",
        assignee_id=stage.assignee_id,
        strategy="pipeline",
        sequence=2,
        status="queued",
    )
    db_session.add(later)
    await db_session.commit()

    await er._advance_pipeline(db_session, stage)

    assert (await db_session.get(Run, later.id)).status == "cancelled"
    assert (await db_session.get(Run, parent.id)).status == "failed"


async def test_advance_pipeline_retrying_keeps_chain(db_session, events):
    """重试等待不收敛父 Run：瞬时抖动不该杀掉整条 pipeline。"""
    _request, parent, stage, _agent = await _fixture(
        db_session, status="retrying", sequence=1
    )

    await er._advance_pipeline(db_session, stage)

    assert (await db_session.get(Run, parent.id)).status == "queued"
    assert any(e[0] == "log" and "自动重试" in e[1] for e in events)


async def test_advance_pipeline_last_stage_completes_parent(db_session, events):
    _request, parent, stage, _agent = await _fixture(
        db_session, status="completed", sequence=3
    )

    await er._advance_pipeline(db_session, stage)

    assert (await db_session.get(Run, parent.id)).status == "completed"


# ---------- 纯函数 ----------


def test_request_text_joins_present_fields():
    req = Request(title="标题", description="描述", expected_output="产出")
    assert er._request_text(req) == "标题\n描述\n期望产出：产出"

    bare = Request(title="只有标题", description=None, expected_output=None)
    assert er._request_text(bare) == "只有标题"


def test_run_id_for_log_is_short_prefix():
    run = Run(id="12345678-aaaa-bbbb-cccc-dddddddddddd")
    assert er._run_id_for_log(run) == "12345678"
