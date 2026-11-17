"""Owner-scoped Agent external-tool grant endpoints."""

from fastapi import APIRouter, status

from app.api.deps import DB, CurrentUser, MemberUser
from app.schemas.external_tool import (
    ExternalGrantCreate,
    ExternalGrantList,
    ExternalGrantOut,
)
from app.services import external_grant_service

router = APIRouter(
    prefix="/agents/{agent_id}/external-tool-grants", tags=["external-tools"]
)


@router.post("", response_model=ExternalGrantOut, status_code=status.HTTP_201_CREATED)
async def create_external_grant(
    agent_id: str,
    payload: ExternalGrantCreate,
    user: CurrentUser,
    _member: MemberUser,
    db: DB,
):
    grant = await external_grant_service.create_grant(
        db,
        agent_id=agent_id,
        user_id=user.id,
        installation_id=payload.installation_id,
        tool_version_id=payload.tool_version_id,
        credential_id=payload.credential_id,
        permissions=payload.permissions,
    )
    await db.commit()
    return external_grant_service._grant_projection(grant)


@router.get("", response_model=ExternalGrantList)
async def list_external_grants(
    agent_id: str, user: CurrentUser, _member: MemberUser, db: DB
):
    items = await external_grant_service.list_agent_grants(
        db, agent_id=agent_id, user_id=user.id
    )
    return {"items": items, "total": len(items)}


@router.delete("/{grant_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_external_grant(
    agent_id: str, grant_id: str, user: CurrentUser, _member: MemberUser, db: DB
):
    await external_grant_service.revoke_grant(
        db, agent_id=agent_id, grant_id=grant_id, user_id=user.id
    )
    await db.commit()
