"""Agent 端点（T020 权限验证载体 + T029+ 完整实现）。

Phase 3：实现 create（member/admin）/ PATCH（归属校验）/ GET 列表——作为权限框架验证载体。
Phase 4：补全 archive / DELETE。
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select

from app.api.deps import DB, CurrentUser, require_role
from app.models.agent import Agent
from app.schemas.agent import AgentCreate, AgentList, AgentOut, AgentUpdate

router = APIRouter(prefix="/agents", tags=["agents"])

MemberUser = Annotated[object, Depends(require_role("admin", "member"))]


@router.get("", response_model=AgentList)
async def list_agents(
    user: CurrentUser, _m: MemberUser, db: DB
):
    """Agent 列表（member/admin 可见自己的；viewer 403）。"""
    rows = (
        await db.execute(
            select(Agent)
            .where(Agent.owner_id == user.id)
            .order_by(Agent.created_at.desc())
        )
    ).scalars().all()
    return {"items": [AgentOut.model_validate(r) for r in rows], "total": len(rows)}


@router.post("", response_model=AgentOut, status_code=201)
async def create_agent(
    payload: AgentCreate, _m: MemberUser, user: CurrentUser, db: DB
):
    """创建 Agent（member/admin；viewer 403）。"""
    agent = Agent(
        owner_id=user.id,
        name=payload.name,
        description=payload.description,
        system_prompt=payload.system_prompt,
        model=payload.model,
        temperature=payload.temperature,
        max_steps=payload.max_steps,
        max_run_seconds=payload.max_run_seconds,
        tools=payload.tools,
        cost_limit_cents=payload.cost_limit_cents,
    )
    db.add(agent)
    await db.commit()
    await db.refresh(agent)
    return AgentOut.model_validate(agent)


@router.patch("/{agent_id}", response_model=AgentOut)
async def update_agent(
    agent_id: str,
    payload: AgentUpdate,
    user: CurrentUser,
    db: DB,
):
    """编辑 Agent（归属校验：非 owner 403）。"""
    agent = await db.get(Agent, agent_id)
    if agent is None:
        from app.api.errors import AppError

        raise AppError("NOT_FOUND", "Agent 不存在")
    if agent.owner_id != user.id:
        from app.api.errors import AppError

        raise AppError("FORBIDDEN", "无权操作他人 Agent")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(agent, field, value)
    await db.commit()
    await db.refresh(agent)
    return AgentOut.model_validate(agent)
