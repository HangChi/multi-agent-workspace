"""Agent 服务（T033）：CRUD + 归档 + 删除前组引用检查 + 归属校验。

对齐 contracts/api.md Agent 部分：
- 删除被组引用的 Agent → 409（仅可归档）
- 归档后不出现在可分配列表（status=active 过滤）

R1：归属判定统一收在 `get_agent`，原先独立的 `_check_owner` 已删除（它只有
`get_agent` 一个调用方，拆成两个函数反而让「同一件事有两种写法」更容易复现）。
"""

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.api.messages import raise_for_missing
from app.models.agent import Agent
from app.models.group_member import GroupMember
from app.schemas.agent import AgentCreate, AgentUpdate
from app.services.tool_service import (
    enabled_tool_specs,
    reconcile_agent_grants,
    validate_grants,
)


async def create_agent(
    db: AsyncSession,
    *,
    user_id: str,
    payload: AgentCreate,
    prebuilt: Agent | None = None,
) -> Agent:
    """创建 Agent（member/admin 已由路由层校验）。

    `prebuilt` 供 005 的「从模板创建 / 克隆」复用同一条落库路径：
    调用方已经构造好 Agent 对象（含归属与全部字段），这里只补归属校验与
    flush，**不再走一遍 Pydantic 校验**——那会让模板路径与手工路径
    出现两套校验语义（FR-009 要求同一套）。

    工具授权仍要校验：`prebuilt.tools` 可能在构造后被改过，也可能来自一个
    工具已下线的模板快照。
    """
    if prebuilt is not None:
        if prebuilt.owner_id != user_id:
            raise_for_missing("agent")
        await validate_grants(db, prebuilt.tools)
        db.add(prebuilt)
        await db.flush()
        return prebuilt

    await validate_grants(db, payload.tools)
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


async def get_agent(
    db: AsyncSession,
    agent_id: str,
    user_id: str,
    *,
    allow_owner_ids: set[str] | frozenset[str] | None = None,
) -> Agent:
    """取单个 Agent（归属校验）：不存在或无权限一律 404（不暴露存在性）。

    R1：Agent 侧的**单一判定点**。此前同一谓词被复制在
    `agent_service._check_owner`、`assignment_service._load_target`、
    `group_service.add_member`、`egress_service._ensure_agent_owner`、
    `request_service.update_request`（auto_agent 校验）五处，
    且放行口径互不一致。

    Args:
        allow_owner_ids: 额外放行的 owner 集合（010 共享项目：参与者可用
            owner 建的 Agent，否则成员拖执行时协作链断在分配那步）。
            不传 = 仅本人，行为与既有调用方逐字节一致。

    刻意**不放行 admin**：016 spec 明确「admin 读他人资源是否放行由各端点
    既有策略决定，本特性不改」。`egress_service` 那处放行 admin 是既有产品
    决策（管理员要能排查出站网络问题），所以它保留自行叠加，不并入本函数
    —— 把 admin 塞进通用谓词等于替产品做决策。
    """
    agent = await db.get(Agent, agent_id)
    if agent is None:
        raise_for_missing("agent")
    if agent.owner_id != user_id and (
        allow_owner_ids is None or agent.owner_id not in allow_owner_ids
    ):
        raise_for_missing("agent")
    return agent


async def list_agents(
    db: AsyncSession,
    *,
    user_id: str,
    status: str | None = None,
    q: str | None = None,
    page: int = 1,
    page_size: int = 20,
    fetch_all: bool = False,
) -> tuple[list[Agent], int]:
    """列表：仅自己的，支持 ?status= &q= 过滤 + 分页。

    返回 `(当前页, 总数)`。**总数是过滤后的全量条数**，不是当前页长度 ——
    前端要靠它算总页数，若返回当页长度，分页器永远只有 1 页。

    `fetch_all=True` 供看板/组页这类「要全部活跃 Agent 当拖拽目标」的场景：
    它们必须拿到全集，否则超过一页的目标会静默消失。此时 page/page_size 忽略。
    """
    conditions = [Agent.owner_id == user_id]
    if status:
        conditions.append(Agent.status == status)
    if q:
        like = f"%{q}%"
        conditions.append(or_(Agent.name.ilike(like), Agent.description.ilike(like)))

    total = (
        await db.execute(select(func.count()).select_from(Agent).where(*conditions))
    ).scalar_one()

    # 加 id 作为 tie-breaker：created_at 只到微秒，同一批创建的 Agent 常常相同，
    # 只按时间排序时 PostgreSQL 不保证稳定顺序 —— 翻页会看到重复项或漏项
    # （实测 3.15 下 4 条同批数据两页取到同一个 id）。
    stmt = (
        select(Agent)
        .where(*conditions)
        .order_by(Agent.created_at.desc(), Agent.id.desc())
    )
    if not fetch_all:
        stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    rows = list((await db.execute(stmt)).scalars().all())
    return rows, total


async def update_agent(
    db: AsyncSession, *, agent_id: str, user_id: str, payload: AgentUpdate
) -> Agent:
    """编辑 Agent（归属校验）。"""
    agent = await get_agent(db, agent_id, user_id)
    changes = payload.model_dump(exclude_unset=True)
    if "tools" in changes:
        await validate_grants(db, changes["tools"])
    else:
        specs = await enabled_tool_specs(db, lock=True)
        reconcile_agent_grants(agent, (spec.name for spec in specs))
    for field, value in changes.items():
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
