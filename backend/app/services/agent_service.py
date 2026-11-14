"""Agent 服务（T033）：CRUD + 归档 + 删除前组引用检查 + 归属校验。

对齐 contracts/api.md Agent 部分：
- 删除被组引用的 Agent → 409（仅可归档）
- 归档后不出现在可分配列表（status=active 过滤）
"""

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.models.agent import Agent
from app.models.group_member import GroupMember
from app.schemas.agent import AgentCreate, AgentUpdate


def _check_owner(agent: Agent, user_id: str) -> None:
    if agent.owner_id != user_id:
        raise AppError("FORBIDDEN", "无权操作他人 Agent")

async def create_agent(db: AsyncSession, *, user_id: str, payload: AgentCreate) -> Agent:
    """创建 Agent（member/admin 已由路由层校验）。"""
    agent = Agent(
        owner_id=user_id,
        name=payload.name,
        description=payload.description,
        system_prompt=payload.system_prompt,
        model=payload.model,
        temperature=payload.temperature,
        max_steps=payload.max_steps,
        max_run_seconds=payload.max_run_seconds,
        tools=payload.tools,
        egress_mode=payload.egress_mode,
        cost_limit_cents=payload.cost_limit_cents,
    )
    db.add(agent)
    await db.flush()
    return agent


async def get_agent(db: AsyncSession, agent_id: str, user_id: str) -> Agent:
    """取单个 Agent（归属校验）。"""
    agent = await db.get(Agent, agent_id)
    if agent is None:
        raise AppError("NOT_FOUND", "Agent 不存在")
    _check_owner(agent, user_id)
    return agent


async def list_agents(
    db: AsyncSession, *, user_id: str, status: str | None = None, q: str | None = None
) -> list[Agent]:
    """列表：仅自己的，支持 ?status= &q= 过滤。"""
    stmt = select(Agent).where(Agent.owner_id == user_id)
    if status:
        stmt = stmt.where(Agent.status == status)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Agent.name.ilike(like), Agent.description.ilike(like)))
    stmt = stmt.order_by(Agent.created_at.desc())
    return list((await db.execute(stmt)).scalars().all())


async def update_agent(
    db: AsyncSession, *, agent_id: str, user_id: str, payload: AgentUpdate
) -> Agent:
    """编辑 Agent（归属校验）。"""
    agent = await get_agent(db, agent_id, user_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(agent, field, value)
    await db.flush()
    return agent


async def archive_agent(db: AsyncSession, *, agent_id: str, user_id: str) -> Agent:
    """归档 Agent（归属校验）。被组引用的 Agent 也允许归档。"""
    agent = await get_agent(db, agent_id, user_id)
    agent.status = "archived"
    await db.flush()
    return agent


async def delete_agent(db: AsyncSession, *, agent_id: str, user_id: str) -> None:
    """删除 Agent：被组引用 → 409（仅可归档）。"""
    agent = await get_agent(db, agent_id, user_id)
    ref_count = (
        await db.execute(
            select(func.count())
            .select_from(GroupMember)
            .where(GroupMember.agent_id == agent_id)
        )
    ).scalar_one()
    if ref_count > 0:
        raise AppError("CONFLICT", "Agent 已被组引用，只能归档不能删除")
    await db.delete(agent)
    await db.flush()
