"""管理员出站黑名单与访问记录端点。"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import DB, CurrentUser, require_role
from app.core.audit import write_audit
from app.schemas.egress import BlacklistIn, BlacklistList, BlacklistOut
from app.schemas.sandbox import EgressAccessList
from app.services import egress_service

router = APIRouter(prefix="/admin", tags=["admin-egress"])
AdminUser = Annotated[object, Depends(require_role("admin"))]


@router.get("/egress-blacklist", response_model=BlacklistList)
async def list_blacklist(_user: CurrentUser, _admin: AdminUser, db: DB):
    rows = await egress_service.list_global_rules(db)
    return {"items": [BlacklistOut.model_validate(row) for row in rows]}


@router.post("/egress-blacklist", response_model=BlacklistOut, status_code=201)
async def create_blacklist(payload: BlacklistIn, user: CurrentUser, _admin: AdminUser, db: DB):
    rule = await egress_service.create_global_rule(db, user_id=user.id, payload=payload)
    await write_audit(db, user_id=user.id, action="egress_blacklist_create", target_type="egress_rule", target_id=rule.id)
    await db.commit()
    await db.refresh(rule)
    return rule


@router.delete("/egress-blacklist/{rule_id:path}", status_code=204)
async def delete_blacklist(rule_id: str, user: CurrentUser, _admin: AdminUser, db: DB):
    await egress_service.delete_global_rule(db, rule_id=rule_id)
    await write_audit(db, user_id=user.id, action="egress_blacklist_delete", target_type="egress_rule", target_id=rule_id)
    await db.commit()


@router.get("/egress-accesses", response_model=EgressAccessList)
async def list_egress_accesses(
    _user: CurrentUser,
    _admin: AdminUser,
    db: DB,
    run_id: str | None = None,
    agent_id: str | None = None,
    action: str | None = Query(default=None, pattern="^(allow|deny)$"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
):
    rows, total, summary = await egress_service.list_accesses(
        db, run_id=run_id, agent_id=agent_id, action=action, page=page, page_size=page_size
    )
    return {"items": rows, "total": total, "summary": summary}
