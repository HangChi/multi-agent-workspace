"""Agent 端点（T035）：CRUD + 归档 + 删除 + 过滤。

对齐 contracts/api.md Agent 部分：
- GET /agents（?status=&q=）→ 200 AgentList
- POST /agents → 201
- PATCH /agents/{id} → 200（归属校验）
- POST /agents/{id}/archive → 200
- DELETE /agents/{id} → 204（被组引用 409）
"""

from fastapi import APIRouter, Query

from app.api.deps import DB, CurrentUser, MemberUser, ResourceId
from app.core.audit import AGENT_TEMPLATE_CREATE, write_audit
from app.models.agent import Agent
from app.schemas.agent import (
    AgentCreate,
    AgentList,
    AgentOut,
    AgentSaveOut,
    AgentUpdate,
)
from app.schemas.agent_template import AgentTemplateFromAgentIn, AgentTemplateOut
from app.services import agent_service, agent_template_service, egress_service

router = APIRouter(prefix="/agents", tags=["agents"])


async def _agent_save_response(db: DB, agent: Agent) -> AgentSaveOut:
    response = AgentSaveOut.model_validate(agent)
    if agent.egress_mode == "restricted":
        # fetch_all：判断「有没有启用的 allow 规则」必须看全集，
        # 被分页截断会把「后几条里有 allow」误判成「一条都没有」。
        rules, _ = await egress_service.list_agent_rules(db, agent.id, fetch_all=True)
        if not any(rule.enabled and rule.action == "allow" for rule in rules):
            response.warning = "限制模式下尚无启用的允许规则，运行时全部出站被拒。"
    return response


@router.get("", response_model=AgentList)
async def list_agents(
    user: CurrentUser,
    _m: MemberUser,
    db: DB,
    status: str | None = Query(default=None, pattern="^(active|archived)$"),
    q: str | None = Query(default=None, max_length=100),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    all: bool = Query(
        default=False,
        description="返回全部命中项（忽略分页）。看板等需要完整目标集合的场景用。",
    ),
):
    """Agent 列表（member/admin 可见自己的；viewer 403）。

    `total` 为过滤后的全量条数，前端据此算总页数。
    """
    rows, total = await agent_service.list_agents(
        db,
        user_id=user.id,
        status=status,
        q=q,
        page=page,
        page_size=page_size,
        fetch_all=all,
    )
    return {"items": [AgentOut.model_validate(r) for r in rows], "total": total}


@router.post("", response_model=AgentSaveOut, status_code=201)
async def create_agent(payload: AgentCreate, _m: MemberUser, user: CurrentUser, db: DB):
    """创建 Agent（member/admin；viewer 403）。"""
    agent = await agent_service.create_agent(db, user_id=user.id, payload=payload)
    await db.refresh(agent)
    await write_audit(
        db,
        user_id=user.id,
        action="agent_create",
        target_type="agent",
        target_id=agent.id,
        detail={"name": agent.name, "model": agent.model},
        strict=True,
    )
    await db.commit()
    return await _agent_save_response(db, agent)


@router.patch("/{agent_id}", response_model=AgentSaveOut)
async def update_agent(
    agent_id: ResourceId,
    payload: AgentUpdate,
    user: CurrentUser,
    db: DB,
):
    """编辑 Agent（归属校验：非 owner 403）。"""
    agent = await agent_service.update_agent(
        db, agent_id=agent_id, user_id=user.id, payload=payload
    )
    await db.refresh(agent)
    await write_audit(
        db,
        user_id=user.id,
        action="agent_update",
        target_type="agent",
        target_id=agent.id,
        detail={"name": agent.name},
        strict=True,
    )
    await db.commit()
    return await _agent_save_response(db, agent)


@router.post("/{agent_id}/archive", response_model=AgentOut)
async def archive_agent(
    agent_id: ResourceId,
    user: CurrentUser,
    db: DB,
):
    """归档 Agent（归属校验；被组引用也允许归档）。"""
    agent = await agent_service.archive_agent(db, agent_id=agent_id, user_id=user.id)
    await db.refresh(agent)
    await write_audit(
        db,
        user_id=user.id,
        action="agent_archive",
        target_type="agent",
        target_id=agent.id,
        strict=True,
    )
    await db.commit()
    return AgentOut.model_validate(agent)


@router.post(
    "/{agent_id}/save-as-template", response_model=AgentTemplateOut, status_code=201
)
async def save_agent_as_template(
    agent_id: ResourceId,
    payload: AgentTemplateFromAgentIn,
    user: CurrentUser,
    db: DB,
):
    """把自己的 Agent 快照成模板（US3 / contracts §1.5）。

    只接受模板的元信息（名称 + 描述），配置全部来自 Agent 当前值。
    归档态的 Agent 也允许保存（US3 AS5）。
    """
    agent = await agent_service.get_agent(db, agent_id, user_id=user.id)
    template = await agent_template_service.save_as_template(
        db, agent=agent, name=payload.name, description=payload.description
    )
    await db.refresh(template)
    await write_audit(
        db,
        user_id=user.id,
        action=AGENT_TEMPLATE_CREATE,
        target_type="agent_template",
        target_id=template.id,
        detail={"name": template.name, "source_agent_id": agent.id},
        strict=True,
    )
    await db.commit()
    out = AgentTemplateOut.model_validate(template)
    out.is_builtin = False
    return out


@router.delete("/{agent_id}", status_code=204)
async def delete_agent(
    agent_id: ResourceId,
    user: CurrentUser,
    db: DB,
):
    """删除 Agent（无组引用时；有组引用 409，仅可归档）。"""
    await agent_service.delete_agent(db, agent_id=agent_id, user_id=user.id)
    await write_audit(
        db,
        user_id=user.id,
        action="agent_delete",
        target_type="agent",
        target_id=agent_id,
        strict=True,
    )
    await db.commit()
