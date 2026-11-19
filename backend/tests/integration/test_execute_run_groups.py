"""组父 Run 的执行分支单测（015 覆盖率补齐）。

组有三种策略，父 Run 的命运完全不同，而这三种都在 `_execute` 开头几十行里：
- **router**：父 Run 自己跑（先让 LLM 选成员）。组里没人时必须是 failed，
  不能静默留在 queued（看板上会永远显示「排队中」）。
- **pipeline**：父 Run 不该入队，若被误入队必须**跳过且不改状态**——改了就是
  抢在阶段收敛前把父 Run 落成终态。
- **fan_out**：父 Run 只做占位收尾（queued → running → completed）。

这些分支不需要真跑 LLM/沙箱，只要把路由函数换掉就能验完。
"""

import uuid

import pytest

from app.models.agent import Agent
from app.models.request import Request
from app.models.run import Run
from app.models.user import User
from app.orchestrator.router import RoutingDecision
from app.tasks import execute_run as er

pytestmark = pytest.mark.integration


@pytest.fixture
def events(monkeypatch):
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(
        er.run_events, "run_failed", lambda rid, err: sent.append(("failed", err))
    )
    monkeypatch.setattr(
        er.run_events,
        "run_completed",
        lambda rid, result: sent.append(("completed", str(result))),
    )
    monkeypatch.setattr(
        er.run_events, "log_line", lambda rid, line: sent.append(("log", line))
    )
    monkeypatch.setattr(
        er.run_events,
        "status_change",
        lambda rid, status: sent.append(("status", status)),
    )
    monkeypatch.setattr(
        er.run_events,
        "cost_update",
        lambda rid, cents: sent.append(("cost", str(cents))),
    )
    return sent


async def _group_run(db, *, strategy, status="queued", with_request=True):
    user = User(
        email=f"grp-{uuid.uuid4().hex[:8]}@test.com",
        password_hash="x" * 60,
        role="member",
        status="active",
    )
    db.add(user)
    await db.flush()
    request = Request(
        owner_id=user.id,
        title="组需求",
        status="running" if with_request else "created",
    )
    db.add(request)
    await db.flush()
    run = Run(
        request_id=request.id,
        owner_id=user.id,
        assignee_type="group",
        assignee_id=str(uuid.uuid4()),
        strategy=strategy,
        status=status,
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)
    return user, request, run


async def test_cancelled_run_is_skipped(db_session, events):
    _user, _req, run = await _group_run(
        db_session, strategy="fan_out", status="cancelled"
    )

    await er._execute(db_session, run, None)

    assert events == []
    assert (await db_session.get(Run, run.id)).status == "cancelled"


# 注：`_execute` 里「需求不存在 → failed」那条分支是防御性的。runs.request_id
# 有外键约束，正常路径造不出「run 存在但 request 没了」的数据，硬造要关约束。
# 这里不伪造，留作防御（同类情况见 egress 的「解析结果不是 dict」）。


async def test_router_without_members_fails(db_session, events, monkeypatch):
    """组里没人可用 → failed（不能留在 queued 让看板永远显示排队中）。"""
    _user, _req, run = await _group_run(db_session, strategy="router")

    async def _no_member(*args, **kwargs):
        return None

    monkeypatch.setattr(er, "route", _no_member)

    await er._execute(db_session, run, None)

    refreshed = await db_session.get(Run, run.id)
    assert refreshed.status == "failed"
    assert "组内无可用成员" in refreshed.error


async def test_router_decision_cost_is_booked(db_session, events, monkeypatch):
    """路由本身也是一次 LLM 调用：成本必须记进本 Run，不能消失在账外。"""
    user, _req, run = await _group_run(db_session, strategy="router")
    agent = Agent(
        owner_id=user.id,
        name=f"picked-{uuid.uuid4().hex[:6]}",
        model="openai/gpt-4o-mini",
        system_prompt="x",
        tools=[],
    )
    db_session.add(agent)
    await db_session.commit()

    async def _pick(*args, **kwargs):
        return RoutingDecision(
            agent=agent, reason="最匹配", cost_cents=2, cost_micros=2_000_000
        )

    monkeypatch.setattr(er, "route", _pick)

    # 路由之后的执行链要碰 LLM/沙箱：换一个「一跑就抛」的 runner，
    # 让测试停在成本已记账、执行未真正开始的那个点上。
    class _FakeRunner:
        """只回一个终态字符串：路由与记账都在它之前完成，这里不需要真执行。"""

        def __init__(self) -> None:
            self.authorize_tool = None

        async def run(self, *args, **kwargs):
            return "completed"

    monkeypatch.setattr(er, "_build_runner", lambda *a, **k: _FakeRunner())

    await er._execute(db_session, run, None)

    refreshed = await db_session.get(Run, run.id)
    assert refreshed.cost_cents == 2
    assert ("cost", "2") in events
    assert any(e[0] == "log" and "路由选择" in e[1] for e in events)


async def test_pipeline_parent_queued_is_left_alone(db_session, events):
    """pipeline 父 Run 不该入队：误入队时跳过，绝不能改状态。"""
    _user, _req, run = await _group_run(db_session, strategy="pipeline")

    await er._execute(db_session, run, None)

    assert (await db_session.get(Run, run.id)).status == "queued"
    assert events == []


async def test_fan_out_parent_completes_as_placeholder(db_session, events):
    """fan_out 父 Run 只是编排占位：状态机要求 queued→running→completed。"""
    _user, _req, run = await _group_run(db_session, strategy="fan_out")

    await er._execute(db_session, run, None)

    refreshed = await db_session.get(Run, run.id)
    assert refreshed.status == "completed"
    assert "由子 Run 完成执行" in refreshed.result["note"]
    assert ("status", "running") in events
    assert any(e[0] == "completed" for e in events)
