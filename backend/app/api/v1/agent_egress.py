"""Agent 级出站规则端点。"""

from fastapi import APIRouter, Query

from app.api.deps import DB, CurrentUser
from app.core.audit import write_audit
from app.models.agent import Agent
from app.schemas.egress import EgressRuleIn, EgressRuleList, EgressRuleOut
from app.services import egress_service

router = APIRouter(prefix="/agents/{agent_id}/egress-rules", tags=["egress"])


@router.get("", response_model=EgressRuleList)
async def list_rules(
    agent_id: str,
    user: CurrentUser,
    db: DB,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    all: bool = Query(
        default=False,
        description="返回全部规则（忽略分页）。编辑表单里需要完整清单时用。",
    ),
):
    await egress_service._ensure_agent_owner(db, agent_id, user.id, user.role)
    agent = await db.get(Agent, agent_id)
    # fetch_all：编辑表单要展示并逐条删除全部规则，分页截断会让用户
    # 看不到也删不掉靠后的规则。
    rows, _total = await egress_service.list_agent_rules(
        db, agent_id, page=page, page_size=page_size, fetch_all=all
    )
    return {"items": rows, "egress_mode": agent.egress_mode if agent else "default"}


@router.post("", response_model=EgressRuleOut, status_code=201)
async def create_rule(agent_id: str, payload: EgressRuleIn, user: CurrentUser, db: DB):
    rule = await egress_service.create_agent_rule(
        db, agent_id=agent_id, user_id=user.id, role=user.role, payload=payload
    )
    await write_audit(
        db,
        user_id=user.id,
        action="egress_rule_create",
        target_type="agent",
        target_id=agent_id,
        detail={"target": payload.target, "target_type": payload.target_type},
    )
    await db.commit()
    await db.refresh(rule)
    return rule


@router.delete("/{rule_id}", status_code=204)
async def delete_rule(agent_id: str, rule_id: str, user: CurrentUser, db: DB):
    rule = await egress_service.delete_agent_rule(
        db, agent_id=agent_id, rule_id=rule_id, user_id=user.id, role=user.role
    )
    await write_audit(
        db,
        user_id=user.id,
        action="egress_rule_delete",
        strict=True,
        target_type="agent",
        target_id=agent_id,
        detail={
            "agent_id": agent_id,
            "rule_id": rule.id,
            "target_type": rule.target_type,
            "target": rule.target,
            "port_range": rule.port_range,
        },
    )
    await db.commit()
