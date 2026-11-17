"""Agent 模板模型层不变量（T012）。

三件事必须在数据库层成立，而不是只靠 schema：

1. **CHECK 约束真的会拦**：模板不得成为绕过 Agent 校验的旁路（FR-022）。
   直接构造 ORM 对象绕过 Pydantic，DB 层仍要拒。
2. **`ON DELETE SET NULL` 双向成立**：删模板 / 删源 Agent 都不带走已创建的 Agent
   （FR-021），且克隆体的 `source_agent_id` 被置空而不是连带删除。
3. **同一 owner 重名被拒、不同 owner 同名允许**（FR-003 的 DB 侧保证）。

所有用例跑完回滚，不污染测试库（db_session 自带 rollback）。
"""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.agent_template import AgentTemplate
from app.models.user import User


async def _user(session: AsyncSession, email: str) -> User:
    user = User(
        email=email,
        password_hash="x" * 60,
        role="member",
        status="active",
    )
    session.add(user)
    await session.flush()
    return user


def _template(**overrides) -> AgentTemplate:
    base = {
        "owner_id": None,
        "name": "t",
        "system_prompt": "p",
        "model": "m",
        "temperature": 0.7,
        "max_steps": 10,
        "max_run_seconds": 300,
        "tools": [],
        "cost_limit_cents": 500,
        "egress_mode": "default",
    }
    base.update(overrides)
    return AgentTemplate(**base)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("name", ""),
        ("system_prompt", ""),
        ("temperature", 2.1),
        ("temperature", -0.1),
        ("max_steps", 0),
        ("max_steps", 101),
        ("max_run_seconds", 0),
        ("max_run_seconds", 86401),
        ("cost_limit_cents", 0),
    ],
)
async def test_check_constraints_reject_out_of_range(
    db_session: AsyncSession, field: str, value
):
    """绕过 Pydantic 直接建行，DB 层仍要拒（FR-022）。"""
    owner = await _user(db_session, f"chk-{field}-{value}@test.com")
    tpl = _template(owner_id=owner.id, **{field: value})
    db_session.add(tpl)
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_owner_scoped_name_uniqueness(db_session: AsyncSession):
    """先 commit 用户，再验证唯一性。

    ⚠️ 不能在同一个事务里「插一条 → 回滚 → 再插」：回滚会把之前 flush 的用户
    一起撤掉，后面的插入会先撞用户外键，看起来像唯一约束失效。
    """
    a = await _user(db_session, "uniq-a@test.com")
    b = await _user(db_session, "uniq-b@test.com")
    # 先取 id：rollback 会让 ORM 对象过期，之后再访问 .id 会触发同步 IO 炸
    # MissingGreenlet。
    a_id, b_id = a.id, b.id
    db_session.add(_template(owner_id=a_id, name="same"))
    await db_session.commit()

    # 同一 owner 重名 → 拒
    db_session.add(_template(owner_id=a_id, name="same"))
    with pytest.raises(IntegrityError, match="uq_agent_templates_owner_name"):
        await db_session.flush()
    await db_session.rollback()

    # 不同 owner 同名 → 允许
    db_session.add(_template(owner_id=b_id, name="same"))
    await db_session.flush()


@pytest.mark.asyncio
async def test_delete_template_keeps_derived_agent_with_null_source(
    db_session: AsyncSession,
):
    """删模板 → 产物 Agent 保留，source_template_id 置 NULL（FR-021）。"""
    owner = await _user(db_session, "del-tpl@test.com")
    tpl = _template(owner_id=owner.id, name="keep-me")
    db_session.add(tpl)
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

    await db_session.delete(tpl)
    await db_session.flush()

    # ⚠️ 必须 refresh：SET NULL 是数据库做的，ORM 身份 map 里还留着旧值，
    # 直接读属性会看到「约束没生效」的假象。
    await db_session.refresh(agent)
    assert agent.source_template_id is None
    assert agent.name == "derived"


@pytest.mark.asyncio
async def test_delete_source_agent_keeps_clone_with_null_source(
    db_session: AsyncSession,
):
    """删源 Agent → 克隆体保留，source_agent_id 置 NULL（FR-021）。"""
    owner = await _user(db_session, "del-src@test.com")
    source = Agent(
        owner_id=owner.id,
        name="source",
        system_prompt="p",
        model="m",
        temperature=0.7,
        max_steps=10,
        max_run_seconds=300,
        tools=[],
        egress_mode="default",
        cost_limit_cents=500,
    )
    db_session.add(source)
    await db_session.flush()
    clone = Agent(
        owner_id=owner.id,
        name="source 副本",
        system_prompt="p",
        model="m",
        temperature=0.7,
        max_steps=10,
        max_run_seconds=300,
        tools=[],
        egress_mode="default",
        cost_limit_cents=500,
        source_agent_id=source.id,
    )
    db_session.add(clone)
    await db_session.flush()

    await db_session.delete(source)
    await db_session.flush()

    await db_session.refresh(clone)
    assert clone.source_agent_id is None
    assert clone.name == "source 副本"


@pytest.mark.asyncio
async def test_builtin_partial_unique_index_exists_in_pg_indexes(
    db_session: AsyncSession,
):
    """模型声明的部分唯一索引必须真的落在库里（防「ORM 里有、DB 里没有」）。"""
    row = (
        await db_session.execute(
            text(
                "SELECT indexdef FROM pg_catalog.pg_indexes "
                "WHERE indexname = 'uq_agent_templates_builtin_name'"
            )
        )
    ).scalar_one()
    assert "UNIQUE" in row
    assert "owner_id IS NULL" in row
