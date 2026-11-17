"""Agent 模板端点（T018）。

对齐 contracts/api.md §1 与 §1.5：

- `GET  /agent-templates`                 列表（内置 + 自己的，scope/q/分页）
- `GET  /agent-templates/{id}`            详情
- `PATCH /agent-templates/{id}`           编辑自己的（内置 403，重名 409）
- `DELETE /agent-templates/{id}`          删除自己的（内置 403）
- `POST /agents/{id}/save-as-template`    把 Agent 快照成模板（见 agents.py）

权限：member/admin；viewer 403（模板含系统提示词，属配置资产）。
事务：service 只 flush，本层 commit（003 审计改造后的约定）。
"""

from fastapi import APIRouter, Query

from app.api.deps import DB, CurrentUser, MemberUser, ResourceId
from app.core.audit import (
    AGENT_TEMPLATE_DELETE,
    AGENT_TEMPLATE_UPDATE,
    write_audit,
)
from app.models.agent_template import AgentTemplate
from app.schemas.agent_template import (
    AgentTemplateList,
    AgentTemplateOut,
    AgentTemplateUpdate,
)
from app.services import agent_template_service

router = APIRouter(prefix="/agent-templates", tags=["agent-templates"])


def _out(template: AgentTemplate) -> AgentTemplateOut:
    response = AgentTemplateOut.model_validate(template)
    response.is_builtin = template.owner_id is None
    return response


@router.get("", response_model=AgentTemplateList)
async def list_templates(
    user: CurrentUser,
    _m: MemberUser,
    db: DB,
    scope: str = Query(default="all", pattern="^(all|mine|builtin)$"),
    q: str | None = Query(default=None, max_length=100),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    """模板列表：内置（全部）+ 自己的。`total` 为过滤后全量条数。"""
    rows, total = await agent_template_service.list_templates(
        db,
        user_id=user.id,
        scope=scope,
        q=q,
        page=page,
        page_size=page_size,
    )
    return {"items": [_out(r) for r in rows], "total": total}


@router.get("/{template_id}", response_model=AgentTemplateOut)
async def get_template(
    template_id: ResourceId,
    user: CurrentUser,
    _m: MemberUser,
    db: DB,
):
    """模板详情。他人模板与不存在返回同一措辞（FR-004）。"""
    template = await agent_template_service.get_template(
        db, template_id, user_id=user.id
    )
    return _out(template)


@router.patch("/{template_id}", response_model=AgentTemplateOut)
async def update_template(
    template_id: ResourceId,
    payload: AgentTemplateUpdate,
    user: CurrentUser,
    db: DB,
):
    """编辑自己的模板（内置 403；重名 409）。"""
    template = await agent_template_service.update_template(
        db, template_id, user_id=user.id, payload=payload
    )
    await db.refresh(template)
    await write_audit(
        db,
        user_id=user.id,
        action=AGENT_TEMPLATE_UPDATE,
        target_type="agent_template",
        target_id=template.id,
        detail={
            "name": template.name,
            "fields": sorted(payload.model_dump(exclude_unset=True).keys()),
        },
        strict=True,
    )
    await db.commit()
    return _out(template)


@router.delete("/{template_id}", status_code=204)
async def delete_template(
    template_id: ResourceId,
    user: CurrentUser,
    db: DB,
):
    """删除自己的模板（内置 403）。

    已由它创建的 Agent 全部保留（外键 SET NULL，FR-021）。
    """
    template = await agent_template_service.get_template(
        db, template_id, user_id=user.id
    )
    name = template.name
    await agent_template_service.delete_template(db, template_id, user_id=user.id)
    await write_audit(
        db,
        user_id=user.id,
        action=AGENT_TEMPLATE_DELETE,
        target_type="agent_template",
        target_id=template_id,
        detail={"name": name},
        strict=True,
    )
    await db.commit()
