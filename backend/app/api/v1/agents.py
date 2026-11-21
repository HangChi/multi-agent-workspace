"""Agent 端点（T035）：CRUD + 归档 + 删除 + 过滤。

对齐 contracts/api.md Agent 部分：
- GET /agents（?status=&q=）→ 200 AgentList
- POST /agents → 201
- PATCH /agents/{id} → 200（归属校验）
- POST /agents/{id}/archive → 200
- DELETE /agents/{id} → 204（被组引用 409）
"""

from fastapi import APIRouter, Query
from sqlalchemy import select

from app.api.deps import DB, CurrentUser, MemberUser, ResourceId
from app.core.audit import (
    AGENT_CLONE,
    AGENT_CREATE_FROM_TEMPLATE,
    AGENT_TEMPLATE_CREATE,
    write_audit,
)
from app.models.agent import Agent
from app.schemas.agent import (
    AgentCreate,
    AgentList,
    AgentOut,
    AgentSaveOut,
    AgentUpdate,
)
from app.schemas.agent_template import (
    AgentFromTemplateIn,
    AgentTemplateFromAgentIn,
    AgentTemplateOut,
)
from app.services import agent_service, agent_template_service, egress_service
from app.services.tool_service import enabled_tool_specs

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


def _filtered_tools_warning(filtered: list[str]) -> str:
    """被过滤工具的固定措辞（contracts §2）。前端只展示，不解析。"""
    return f"模板中的工具已下线，未启用：{'、'.join(filtered)}"


@router.post(
    "/from-template/{template_id}", response_model=AgentSaveOut, status_code=201
)
async def create_agent_from_template(
    template_id: ResourceId,
    payload: AgentFromTemplateIn,
    _m: MemberUser,
    user: CurrentUser,
    db: DB,
):
    """从模板创建 Agent（US1 / contracts §2）。

    请求体是**完整的 AgentCreate**：模板值打底，请求体里出现的字段覆盖。
    直接带模板值提交就是「一键确认」；改两个字段就是「基于模板微调」——
    两条路走同一条校验（FR-009）。

    模板里已下线/未启用的工具被过滤，工具名进 `warning`（FR-008）——
    这也是本端点用 `AgentFromTemplateIn` 而非 `AgentCreate` 的原因：
    后者的 tools 挂著目录白名单校验器，会把「模板引用了一个已下线工具」
    变成用户没打过的 422，过滤逻辑永远走不到。
    """
    template = await agent_template_service.get_template(
        db, template_id, user_id=user.id
    )
    specs = await enabled_tool_specs(db)
    enabled = [spec.name for spec in specs]
    draft, filtered = agent_template_service.instantiate(
        template, payload=payload.model_dump(), enabled_tools=enabled
    )
    # owner_id 不在模板快照里（模板可被任何成员使用），由端点按当前用户补；
    # 来源列同理——它是「这个 Agent 用哪个模板建的」的追溯，不是模板的字段。
    draft.owner_id = user.id
    draft.source_template_id = template.id
    agent = await agent_service.create_agent(
        db,
        user_id=user.id,
        payload=AgentCreate(
            name=draft.name,
            description=draft.description,
            system_prompt=draft.system_prompt,
            model=draft.model,
            temperature=draft.temperature,
            max_steps=draft.max_steps,
            max_run_seconds=draft.max_run_seconds,
            tools=draft.tools,
            cost_limit_cents=draft.cost_limit_cents,
            egress_mode=draft.egress_mode,
        ),
        prebuilt=draft,
    )
    await db.refresh(agent)
    await write_audit(
        db,
        user_id=user.id,
        action=AGENT_CREATE_FROM_TEMPLATE,
        target_type="agent",
        target_id=agent.id,
        detail={
            "name": agent.name,
            "template_id": template.id,
            "filtered_tools": filtered,
        },
        strict=True,
    )
    await db.commit()
    response = await _agent_save_response(db, agent)
    if filtered:
        response.warning = _filtered_tools_warning(filtered)
    return response


@router.post("/{agent_id}/clone", response_model=AgentSaveOut, status_code=201)
async def clone_agent(
    agent_id: ResourceId,
    _m: MemberUser,
    user: CurrentUser,
    db: DB,
):
    """一键克隆 Agent（US2 / contracts §3）。

    只拷 10 个配置字段；运行历史、组引用、外部工具授权、出站规则一律不复制
    （FR-011）。源 Agent 被组引用也允许克隆（FR-013）。
    """
    source = await agent_service.get_agent(db, agent_id, user_id=user.id)

    used_names = set(
        (
            await db.execute(select(Agent.name).where(Agent.owner_id == user.id))
        ).scalars()
    )
    specs = await enabled_tool_specs(db)
    enabled = {spec.name for spec in specs}
    clone = agent_template_service.clone_agent(
        source, owner_id=user.id, used_names=used_names
    )
    # 源 Agent 的工具集可能包含创建后被下线的工具：剥掉它们并在 warning 里说明，
    # 而不是让克隆失败（与 FR-008 同一种哲学）。
    stale = [t for t in (clone.tools or []) if t not in enabled]
    if stale:
        clone.tools = [t for t in clone.tools if t in enabled]
    clone.source_agent_id = source.id

    agent = await agent_service.create_agent(
        db,
        user_id=user.id,
        payload=AgentCreate(name=clone.name, system_prompt=clone.system_prompt),
        prebuilt=clone,
    )
    await db.refresh(agent)
    await write_audit(
        db,
        user_id=user.id,
        action=AGENT_CLONE,
        target_type="agent",
        target_id=agent.id,
        detail={"name": agent.name, "source_agent_id": source.id},
        strict=True,
    )
    await db.commit()
    response = await _agent_save_response(db, agent)
    if stale:
        response.warning = _filtered_tools_warning(stale)
    return response


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
    return agent_template_service.to_out(template)


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
