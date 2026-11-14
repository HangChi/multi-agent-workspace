"""pipeline 策略测试：阶段顺序、产出传递、失败中断、父 Run 收敛。

pipeline = 组内成员按 position 顺序串联：A 的产出作为 B 的输入。
与 fan_out（并发）相比，链路是串的——任一段失败必须停止后续并让父 Run 收口，
否则用户会看到「组任务已完成但只跑了前两段」。
"""

import uuid

import pytest

from app.core.security import hash_password
from app.executors.llm import LLMResponse
from app.models.agent import Agent
from app.models.group import AgentGroup
from app.models.group_member import GroupMember
from app.models.request import Request
from app.models.run import Run
from app.models.user import User
from app.tasks.execute_run import _execute
from tests.integration.test_execution_flow import ScriptedLLM


class _Recorder:
    """替身 _enqueue：记录链式推进时被入队的 run_id。"""

    def __init__(self) -> None:
        self.run_ids: list[str] = []

    def __call__(self, run_ids: list[str]) -> None:
        self.run_ids.extend(run_ids)


@pytest.fixture
def enqueued(monkeypatch) -> _Recorder:
    recorder = _Recorder()
    monkeypatch.setattr("app.tasks.execute_run._enqueue", recorder)
    return recorder


async def _make_world(db) -> tuple[User, Request, AgentGroup, list[Agent]]:
    """造：用户 + 需求（running）+ pipeline 组（两个成员）。"""
    user = User(
        email=f"pipe-{uuid.uuid4().hex[:8]}@test.com",
        password_hash=hash_password("TestPass123!"),
        role="member",
        status="active",
    )
    db.add(user)
    await db.flush()

    request = Request(owner_id=user.id, title="流水线需求", priority="normal", status="running")
    db.add(request)

    group = AgentGroup(
        owner_id=user.id, name="流水线组", routing_strategy="pipeline", status="active"
    )
    db.add(group)
    await db.flush()

    agents: list[Agent] = []
    for index, (name, prompt) in enumerate(
        [("写手", "你是写手，负责出初稿"), ("编辑", "你是编辑，负责润色初稿")], start=1
    ):
        agent = Agent(
            owner_id=user.id,
            name=name,
            description=f"{name}的职责",
            system_prompt=prompt,
            model="gpt-4o-mini",
            status="active",
        )
        db.add(agent)
        await db.flush()
        db.add(GroupMember(group_id=group.id, agent_id=agent.id, position=index))
        agents.append(agent)

    await db.commit()
    return user, request, group, agents


async def _build_chain(db, request: Request, user: User, agents: list[Agent]) -> tuple[Run, list[Run]]:
    """按 assignment_service 的规则造 pipeline Run 链（直接建，不经过 API）。"""
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

    children: list[Run] = []
    for index, agent in enumerate(agents, start=1):
        child = Run(
            request_id=request.id,
            parent_run_id=parent.id,
            owner_id=user.id,
            assignee_type="agent",
            assignee_id=agent.id,
            strategy="pipeline",
            sequence=index,
            status="queued",
        )
        db.add(child)
        children.append(child)
    await db.commit()
    return parent, children


def _script_llm(monkeypatch, responses: list[LLMResponse]) -> ScriptedLLM:
    fake = ScriptedLLM(responses)
    monkeypatch.setattr("app.tasks.execute_run.LiteLLMClient.chat", fake.chat)
    return fake


async def test_first_stage_triggers_second(db_session, monkeypatch, enqueued):
    """第 1 阶段完成 → 触发第 2 阶段（而不是一次性全部入队）。"""
    user, request, _group, agents = await _make_world(db_session)
    parent, children = await _build_chain(db_session, request, user, agents)
    _script_llm(monkeypatch, [LLMResponse(content="初稿内容", cost_cents=2)])

    await _execute(db_session, children[0], agents[0])
    await db_session.refresh(children[0])

    assert children[0].status == "completed"
    assert enqueued.run_ids == [children[1].id], "应只触发下一阶段"
    # 父 Run 还没完成（还有后续阶段）
    await db_session.refresh(parent)
    assert parent.status == "queued"


async def test_second_stage_receives_previous_output(db_session, monkeypatch, enqueued):
    """第 2 阶段的输入包含第 1 阶段产出（串联的核心语义）。"""
    user, request, _group, agents = await _make_world(db_session)
    _parent, children = await _build_chain(db_session, request, user, agents)

    # 先让第 1 阶段有产出
    _script_llm(monkeypatch, [LLMResponse(content="初稿：关于 XX 的三个要点", cost_cents=2)])
    await _execute(db_session, children[0], agents[0])

    # 第 2 阶段：捕获它收到的 messages
    fake = _script_llm(monkeypatch, [LLMResponse(content="润色后的终稿", cost_cents=1)])
    await _execute(db_session, children[1], agents[1])

    user_prompt = fake.calls[0][0][1]["content"]
    assert "初稿：关于 XX 的三个要点" in user_prompt
    assert "上一阶段产出" in user_prompt


async def test_last_stage_finalizes_parent(db_session, monkeypatch, enqueued):
    """最后一段完成 → 父 Run 收敛为 completed，并记录各阶段摘要。"""
    user, request, _group, agents = await _make_world(db_session)
    parent, children = await _build_chain(db_session, request, user, agents)

    _script_llm(monkeypatch, [LLMResponse(content="初稿", cost_cents=2)])
    await _execute(db_session, children[0], agents[0])
    _script_llm(monkeypatch, [LLMResponse(content="终稿", cost_cents=1)])
    await _execute(db_session, children[1], agents[1])

    await db_session.refresh(parent)
    assert parent.status == "completed"
    assert parent.result["strategy"] == "pipeline"
    stages = parent.result["stages"]
    assert [s["sequence"] for s in stages] == [1, 2]
    assert [s["cost_cents"] for s in stages] == [2, 1]


async def test_failure_stops_chain_and_fails_parent(db_session, monkeypatch, enqueued):
    """任一段失败 → 取消剩余阶段，父 Run 标 failed（不能假装成功）。"""
    user, request, _group, agents = await _make_world(db_session)
    parent, children = await _build_chain(db_session, request, user, agents)

    # 第 1 阶段：LLM 直接抛错（脚本为空 → ScriptedLLM 抛 RuntimeError）
    _script_llm(monkeypatch, [])
    await _execute(db_session, children[0], agents[0])

    await db_session.refresh(children[0])
    await db_session.refresh(children[1])
    await db_session.refresh(parent)

    assert children[0].status == "failed"
    assert children[1].status == "cancelled", "失败后剩余阶段应被取消"
    assert parent.status == "failed"
    assert "pipeline" in (parent.error or "")
    assert enqueued.run_ids == [], "失败后不应触发后续阶段"


async def test_parent_finalize_is_idempotent(db_session, monkeypatch, enqueued):
    """收敛函数幂等：父 Run 已终态时再次调用不改写（重试/重复消费安全）。"""
    from app.tasks.execute_run import _finalize_parent

    user, request, _group, agents = await _make_world(db_session)
    parent, children = await _build_chain(db_session, request, user, agents)

    _script_llm(monkeypatch, [LLMResponse(content="初稿", cost_cents=1)])
    await _execute(db_session, children[0], agents[0])
    _script_llm(monkeypatch, [LLMResponse(content="终稿", cost_cents=1)])
    await _execute(db_session, children[1], agents[1])

    await db_session.refresh(parent)
    assert parent.status == "completed"
    first_result = dict(parent.result)

    # 再次收敛（模拟重复触发）
    await _finalize_parent(db_session, parent.id, failed=False, error=None, stages=children)
    await db_session.refresh(parent)
    assert parent.result == first_result, "已完成父 Run 不应被二次改写"
