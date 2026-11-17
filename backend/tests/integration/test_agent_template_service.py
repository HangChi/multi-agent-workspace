"""Agent 模板服务层（T015）。

服务层测试放 integration：它需要真实 PostgreSQL 来验证唯一约束、SET NULL 与
归属过滤——这些恰恰是不能用假对象糊过去的部分。

锁住的行为：

- 归属：只能看/改/删自己的模板；他人模板 NOT_FOUND 且**措辞与不存在一致**
  （FR-004，不泄露存在性）。
- 重名：同一 owner 409 CONFLICT；不同 owner 允许。
- 内置模板：全员可见，但 update/delete 一律 403 FORBIDDEN。
- `save_as_template`：快照 10 个字段；源 Agent 归档也允许。
- scope 过滤与 total 是过滤后的全量条数。
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.models.agent import Agent
from app.models.agent_template import AgentTemplate
from app.models.user import User
from app.schemas.agent_template import AgentTemplateCreate, AgentTemplateUpdate
from app.services import agent_template_service as svc


async def _user(session: AsyncSession, email: str) -> User:
    user = User(email=email, password_hash="x" * 60, role="member", status="active")
    session.add(user)
    await session.flush()
    return user


def _agent(owner_id: str, name: str = "a") -> Agent:
    return Agent(
        owner_id=owner_id,
        name=name,
        description="agent-desc",
        system_prompt="agent-prompt",
        model="agent-model",
        temperature=0.3,
        max_steps=7,
        max_run_seconds=123,
        tools=["calculate"],
        egress_mode="restricted",
        cost_limit_cents=321,
    )


def _create_payload(**overrides) -> AgentTemplateCreate:
    base = {"name": "tpl", "description": "d", "system_prompt": "p", "model": "m"}
    base.update(overrides)
    return AgentTemplateCreate(**base)


@pytest.mark.asyncio
async def test_create_and_get_round_trip(db_session: AsyncSession):
    owner = await _user(db_session, "svc-create@test.com")
    tpl = await svc.create_template(
        db_session, user_id=owner.id, payload=_create_payload()
    )
    await db_session.flush()

    fetched = await svc.get_template(db_session, tpl.id, user_id=owner.id)
    assert fetched.id == tpl.id
    assert fetched.name == "tpl"
    assert fetched.model == "m"


@pytest.mark.asyncio
async def test_foreign_template_read_is_not_found_with_identical_message(
    db_session: AsyncSession,
):
    owner = await _user(db_session, "svc-owner@test.com")
    stranger = await _user(db_session, "svc-stranger@test.com")
    tpl = await svc.create_template(
        db_session, user_id=owner.id, payload=_create_payload()
    )
    await db_session.flush()

    with pytest.raises(AppError) as exc:
        await svc.get_template(db_session, tpl.id, user_id=stranger.id)
    assert exc.value.code == "NOT_FOUND"
    assert exc.value.message == "模板不存在"

    # 与「真的不存在」逐字一致
    with pytest.raises(AppError) as missing:
        await svc.get_template(
            db_session, "00000000-0000-0000-0000-000000000000", user_id=stranger.id
        )
    assert missing.value.message == exc.value.message
    assert missing.value.code == exc.value.code


@pytest.mark.asyncio
async def test_duplicate_name_conflicts_per_owner(db_session: AsyncSession):
    a = await _user(db_session, "svc-a@test.com")
    b = await _user(db_session, "svc-b@test.com")
    await svc.create_template(db_session, user_id=a.id, payload=_create_payload())
    await db_session.flush()

    with pytest.raises(AppError) as exc:
        await svc.create_template(db_session, user_id=a.id, payload=_create_payload())
    assert exc.value.code == "CONFLICT"
    assert exc.value.message == "同名模板已存在"

    # 不同 owner 可以同名
    other = await svc.create_template(
        db_session, user_id=b.id, payload=_create_payload()
    )
    await db_session.flush()
    assert other.owner_id == b.id


@pytest.mark.asyncio
async def test_update_duplicate_name_conflicts(db_session: AsyncSession):
    owner = await _user(db_session, "svc-dup-update@test.com")
    await svc.create_template(
        db_session, user_id=owner.id, payload=_create_payload(name="one")
    )
    tpl = await svc.create_template(
        db_session, user_id=owner.id, payload=_create_payload(name="two")
    )
    await db_session.flush()

    with pytest.raises(AppError) as exc:
        await svc.update_template(
            db_session,
            tpl.id,
            user_id=owner.id,
            payload=AgentTemplateUpdate(name="one"),
        )
    assert exc.value.code == "CONFLICT"


@pytest.mark.asyncio
async def test_builtin_templates_are_read_only(db_session: AsyncSession):
    everyone = await _user(db_session, "svc-everyone@test.com")
    builtin = AgentTemplate(owner_id=None, name="内置", system_prompt="p", model="m")
    db_session.add(builtin)
    await db_session.flush()

    # 任何成员都能读到
    listed, total = await svc.list_templates(
        db_session, user_id=everyone.id, scope="all"
    )
    assert builtin.id in {t.id for t in listed}
    assert total >= 1

    with pytest.raises(AppError) as upd:
        await svc.update_template(
            db_session,
            builtin.id,
            user_id=everyone.id,
            payload=AgentTemplateUpdate(name="x"),
        )
    assert upd.value.code == "FORBIDDEN"

    with pytest.raises(AppError) as dele:
        await svc.delete_template(db_session, builtin.id, user_id=everyone.id)
    assert dele.value.code == "FORBIDDEN"


@pytest.mark.asyncio
async def test_list_scope_filters_and_total_is_full_count(db_session: AsyncSession):
    owner = await _user(db_session, "svc-list@test.com")
    for i in range(3):
        await svc.create_template(
            db_session, user_id=owner.id, payload=_create_payload(name=f"mine-{i}")
        )
    db_session.add(
        AgentTemplate(owner_id=None, name="b1", system_prompt="p", model="m")
    )
    await db_session.flush()

    mine, total_mine = await svc.list_templates(
        db_session, user_id=owner.id, scope="mine"
    )
    assert total_mine == 3
    assert all(t.owner_id == owner.id for t in mine)

    builtin_only, total_builtin = await svc.list_templates(
        db_session, user_id=owner.id, scope="builtin"
    )
    assert total_builtin >= 1
    assert all(t.owner_id is None for t in builtin_only)

    # total 是过滤后全量，不是当页长度：page_size=2 时仍应报 3
    page, total_page = await svc.list_templates(
        db_session, user_id=owner.id, scope="mine", page=1, page_size=2
    )
    assert len(page) == 2
    assert total_page == 3


@pytest.mark.asyncio
async def test_save_as_template_snapshots_all_ten_fields(db_session: AsyncSession):
    owner = await _user(db_session, "svc-save@test.com")
    agent = _agent(owner.id)
    db_session.add(agent)
    await db_session.flush()

    tpl = await svc.save_as_template(
        db_session, agent=agent, name="快照", description="来自 Agent"
    )
    await db_session.flush()

    assert tpl.owner_id == owner.id
    assert tpl.name == "快照"
    assert tpl.description == "来自 Agent"
    assert tpl.system_prompt == "agent-prompt"
    assert tpl.model == "agent-model"
    assert tpl.temperature == 0.3
    assert tpl.max_steps == 7
    assert tpl.max_run_seconds == 123
    assert tpl.tools == ["calculate"]
    assert tpl.cost_limit_cents == 321
    assert tpl.egress_mode == "restricted"
    # 模板没有 status 语义
    assert not hasattr(tpl, "status") or "status" not in AgentTemplate.__table__.columns


@pytest.mark.asyncio
async def test_save_as_template_from_archived_agent_is_allowed(
    db_session: AsyncSession,
):
    owner = await _user(db_session, "svc-archived@test.com")
    agent = _agent(owner.id)
    agent.status = "archived"
    db_session.add(agent)
    await db_session.flush()

    tpl = await svc.save_as_template(db_session, agent=agent, name="归档来的")
    await db_session.flush()
    assert tpl.name == "归档来的"


@pytest.mark.asyncio
async def test_snapshot_isolation_both_directions(db_session: AsyncSession):
    """改 Agent 不改模板；改模板不改 Agent（FR-002）。"""
    owner = await _user(db_session, "svc-iso@test.com")
    agent = _agent(owner.id)
    db_session.add(agent)
    await db_session.flush()
    tpl = await svc.save_as_template(db_session, agent=agent, name="iso")
    await db_session.flush()

    # 1. 改 Agent → 模板不变
    agent.system_prompt = "changed"
    agent.temperature = 1.9
    await db_session.flush()
    await db_session.refresh(tpl)
    assert tpl.system_prompt == "agent-prompt"
    assert tpl.temperature == 0.3

    # 2. 改模板 → Agent 不变
    await svc.update_template(
        db_session,
        tpl.id,
        user_id=owner.id,
        payload=AgentTemplateUpdate(system_prompt="tpl-changed", temperature=0.1),
    )
    await db_session.flush()
    await db_session.refresh(agent)
    assert agent.system_prompt == "changed"
    assert agent.temperature == 1.9


@pytest.mark.asyncio
async def test_delete_template_keeps_derived_agent(db_session: AsyncSession):
    owner = await _user(db_session, "svc-del@test.com")
    tpl = await svc.create_template(
        db_session, user_id=owner.id, payload=_create_payload()
    )
    await db_session.flush()
    agent = Agent(
        owner_id=owner.id,
        name="derived",
        system_prompt="p",
        model="m",
        temperature=0.7,
        max_steps=10,
        max_run_seconds=300,
        tools=[],
        egress_mode="default",
        cost_limit_cents=500,
        source_template_id=tpl.id,
    )
    db_session.add(agent)
    await db_session.flush()

    await svc.delete_template(db_session, tpl.id, user_id=owner.id)
    await db_session.flush()
    await db_session.refresh(agent)
    assert agent.source_template_id is None
