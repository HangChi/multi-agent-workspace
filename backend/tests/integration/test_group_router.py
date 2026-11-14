"""router 策略测试：LLM 选成员、解析容错、降级路径。

router 与 fan_out（全员并发）/ pipeline（顺序串联）的区别是「只选一个」，
选错或选不出来不能把整个任务搞挂——所以所有异常路径都必须有降级。
"""

import uuid

import pytest

from app.core.security import hash_password
from app.models.agent import Agent
from app.models.group import AgentGroup
from app.models.group_member import GroupMember
from app.models.user import User
from app.orchestrator.router import _describe, _parse_index, load_active_members, route


class FakeLLM:
    """可编程的 LLM：返回固定内容或抛异常。"""

    def __init__(self, content: str = "1", cost_cents: int = 0, error: Exception | None = None):
        self.content = content
        self.cost_cents = cost_cents
        self.error = error
        self.calls: list[list[dict]] = []

    async def chat(self, messages, **_kwargs):
        from app.executors.llm import LLMResponse

        self.calls.append(messages)
        if self.error is not None:
            raise self.error
        return LLMResponse(content=self.content, cost_cents=self.cost_cents)


class TestParseIndex:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("2", 2),
            ("第 3 个", 3),
            ("1.", 1),
            ("  4  ", 4),
        ],
    )
    def test_parses_valid(self, text: str, expected: int):
        assert _parse_index(text, 5) == expected

    @pytest.mark.parametrize("text", ["", "  ", "abc", "0", "9", "-1", None])
    def test_rejects_invalid(self, text):
        assert _parse_index(text, 5) is None


class TestDescribe:
    def test_lists_name_and_description(self):
        agents = [
            Agent(name="写手", description="负责撰写文案"),
            Agent(name="审校", description=None),
        ]
        text = _describe(agents)
        assert "1. 写手：负责撰写文案" in text
        assert "2. 审校：（无描述）" in text


# ---------- 集成（真实 DB）----------


async def _make_group(db, *, member_count: int, owner: User) -> AgentGroup:
    group = AgentGroup(owner_id=owner.id, name="路由组", routing_strategy="router", status="active")
    db.add(group)
    await db.flush()
    for index in range(1, member_count + 1):
        agent = Agent(
            owner_id=owner.id,
            name=f"专家{index}",
            description=f"擅长领域 {index}",
            system_prompt="你是专家",
            model="gpt-4o-mini",
            status="active",
        )
        db.add(agent)
        await db.flush()
        db.add(GroupMember(group_id=group.id, agent_id=agent.id, position=index))
    await db.flush()
    return group


@pytest.fixture
async def owner(db_session) -> User:
    user = User(
        email=f"router-{uuid.uuid4().hex[:8]}@test.com",
        password_hash=hash_password("TestPass123!"),
        role="member",
        status="active",
    )
    db_session.add(user)
    await db_session.commit()
    return user


async def test_single_member_skips_llm(db_session, owner):
    """只有一个成员时不需要花 LLM 成本。"""
    group = await _make_group(db_session, member_count=1, owner=owner)
    llm = FakeLLM(content="1", cost_cents=99)

    decision = await route(db_session, group_id=group.id, request_text="随便", llm=llm)

    assert decision is not None
    assert decision.agent.name == "专家1"
    assert decision.cost_cents == 0
    assert llm.calls == []


async def test_selects_member_by_llm_choice(db_session, owner):
    group = await _make_group(db_session, member_count=3, owner=owner)
    llm = FakeLLM(content="2", cost_cents=3)

    decision = await route(db_session, group_id=group.id, request_text="写个文案", llm=llm)

    assert decision is not None
    assert decision.agent.name == "专家2"
    assert decision.cost_cents == 3  # 路由成本计入 Run，不能丢
    assert "路由" in decision.reason
    # 提示里应包含全部候选
    prompt = llm.calls[0][-1]["content"]
    assert "专家1" in prompt and "专家3" in prompt


async def test_unparsable_output_falls_back_to_first(db_session, owner):
    """模型乱答（"我觉得都行"）→ 降级第一个，不失败。"""
    group = await _make_group(db_session, member_count=3, owner=owner)
    llm = FakeLLM(content="我觉得第 3 个不错", cost_cents=1)

    decision = await route(db_session, group_id=group.id, request_text="x", llm=llm)

    assert decision is not None
    assert decision.agent.name == "专家3"  # 能解析出 3
    assert decision.cost_cents == 1


async def test_garbage_output_falls_back_to_first(db_session, owner):
    group = await _make_group(db_session, member_count=3, owner=owner)
    llm = FakeLLM(content="完全无法解析", cost_cents=1)

    decision = await route(db_session, group_id=group.id, request_text="x", llm=llm)

    assert decision is not None
    assert decision.agent.name == "专家1"
    assert "无法解析" in decision.reason


async def test_llm_error_falls_back_to_first(db_session, owner):
    """路由模型挂了也不能让整个任务失败。"""
    from app.executors.llm import LLMClientError

    group = await _make_group(db_session, member_count=2, owner=owner)
    llm = FakeLLM(error=LLMClientError("LLM 调用失败: timeout"))

    decision = await route(db_session, group_id=group.id, request_text="x", llm=llm)

    assert decision is not None
    assert decision.agent.name == "专家1"
    assert "降级" in decision.reason


async def test_out_of_range_index_falls_back(db_session, owner):
    group = await _make_group(db_session, member_count=2, owner=owner)
    llm = FakeLLM(content="7")

    decision = await route(db_session, group_id=group.id, request_text="x", llm=llm)

    assert decision is not None
    assert decision.agent.name == "专家1"


async def test_no_active_members_returns_none(db_session, owner):
    group = await _make_group(db_session, member_count=0, owner=owner)
    assert await route(db_session, group_id=group.id, request_text="x") is None


async def test_archived_members_excluded(db_session, owner):
    group = await _make_group(db_session, member_count=2, owner=owner)
    members = await load_active_members(db_session, group.id)
    members[1].status = "archived"
    await db_session.commit()

    active = await load_active_members(db_session, group.id)
    assert [a.name for a in active] == ["专家1"]
