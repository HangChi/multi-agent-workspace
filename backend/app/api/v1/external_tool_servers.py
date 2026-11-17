"""Administrator endpoints for external MCP server registration and validation."""

from fastapi import APIRouter, Query, status
from sqlalchemy import select

from app.api.deps import DB, AdminUser, CurrentUser
from app.api.errors import AppError
from app.models.external_credential import ExternalCredential
from app.schemas.external_tool import (
    CredentialOut,
    CredentialRotate,
    ExternalServerCreate,
    ExternalServerList,
    ExternalServerOut,
    ExternalServerUpdate,
    ListingOut,
    ReviewDecision,
)
from app.services import (
    external_credential_service,
    external_tool_service,
    marketplace_service,
)
from app.services.external_audit_service import write_external_audit
from app.tasks.validate_external_server import validate_external_server

router = APIRouter(prefix="/external-tool-servers", tags=["external-tools"])


@router.post("", response_model=ExternalServerOut, status_code=status.HTTP_201_CREATED)
async def create_external_server(
    payload: ExternalServerCreate, user: CurrentUser, _admin: AdminUser, db: DB
):
    server = await external_tool_service.create_server(
        db, owner_id=user.id, created_by=user.id, payload=payload
    )
    validate_external_server.delay(server.id, user.id)
    return external_tool_service.server_projection(server)


@router.get("", response_model=ExternalServerList)
async def list_external_servers(
    user: CurrentUser,
    _admin: AdminUser,
    db: DB,
    status_filter: str | None = Query(default=None, alias="status", max_length=32),
    q: str | None = Query(default=None, max_length=100),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
):
    items, total = await external_tool_service.list_servers(
        db,
        owner_id=user.id,
        is_admin=True,
        status=status_filter,
        q=q,
        page=page,
        page_size=page_size,
    )
    return {"items": items, "total": total}


@router.get("/{server_id}", response_model=ExternalServerOut)
async def get_external_server(
    server_id: str, user: CurrentUser, _admin: AdminUser, db: DB
):
    server = await external_tool_service.get_server(
        db, server_id=server_id, owner_id=user.id, is_admin=True
    )
    return external_tool_service.server_projection(server)


@router.patch("/{server_id}", response_model=ExternalServerOut)
async def update_external_server(
    server_id: str,
    payload: ExternalServerUpdate,
    user: CurrentUser,
    _admin: AdminUser,
    db: DB,
):
    server = await external_tool_service.get_server(
        db, server_id=server_id, owner_id=user.id, is_admin=True
    )
    server = await external_tool_service.update_server(
        db, server=server, payload=payload
    )
    return external_tool_service.server_projection(server)


@router.post("/{server_id}/validate", status_code=status.HTTP_202_ACCEPTED)
async def validate_external_server_endpoint(
    server_id: str, user: CurrentUser, _admin: AdminUser, db: DB
):
    await external_tool_service.get_server(
        db, server_id=server_id, owner_id=user.id, is_admin=True
    )
    task = validate_external_server.delay(server_id, user.id)
    return {"status": "queued", "task_id": getattr(task, "id", None)}


@router.post("/{server_id}/credentials/rotate", response_model=CredentialOut)
async def rotate_external_credential(
    server_id: str,
    payload: CredentialRotate,
    user: CurrentUser,
    _admin: AdminUser,
    db: DB,
):
    server = await external_tool_service.get_server(
        db, server_id=server_id, owner_id=user.id, is_admin=True
    )
    credential = await db.scalar(
        select(ExternalCredential)
        .where(
            ExternalCredential.server_id == server.id,
            ExternalCredential.scope_owner_id == server.owner_id,
            ExternalCredential.status == "active",
        )
        .order_by(ExternalCredential.created_at.desc())
    )
    if credential is None:
        raise AppError("NOT_FOUND", "没有可轮换的活动凭证")
    replacement = await external_credential_service.rotate_credential(
        db,
        old_credential=credential,
        created_by=user.id,
        label=payload.label,
        value=payload.value,
        kind=payload.kind,
    )
    await write_external_audit(
        db,
        user_id=user.id,
        action="external_credential_rotate",
        target_type="external_credential",
        target_id=replacement.id,
        detail={"server_id": server.id, "previous_credential_id": credential.id},
    )
    await db.commit()
    await db.refresh(replacement)
    return replacement


@router.post(
    "/{server_id}/credentials/{credential_id}/revoke", response_model=CredentialOut
)
async def revoke_external_credential(
    server_id: str,
    credential_id: str,
    user: CurrentUser,
    _admin: AdminUser,
    db: DB,
):
    server = await external_tool_service.get_server(
        db, server_id=server_id, owner_id=user.id, is_admin=True
    )
    credential = await db.scalar(
        select(ExternalCredential).where(
            ExternalCredential.id == credential_id,
            ExternalCredential.server_id == server.id,
            ExternalCredential.scope_owner_id == server.owner_id,
        )
    )
    if credential is None:
        raise AppError("NOT_FOUND", "外部工具凭证不存在")
    await external_credential_service.revoke_credential(db, credential)
    await write_external_audit(
        db,
        user_id=user.id,
        action="external_credential_revoke",
        target_type="external_credential",
        target_id=credential.id,
        detail={"server_id": server.id},
    )
    await db.commit()
    await db.refresh(credential)
    return credential


@router.post("/{server_id}/tools/{tool_version_id}/approve", response_model=ListingOut)
async def approve_external_tool(
    server_id: str,
    tool_version_id: str,
    payload: ReviewDecision,
    _reviewer: AdminUser,
    db: DB,
    user: CurrentUser,
):
    return await marketplace_service.approve_listing(
        db,
        server_id=server_id,
        tool_version_id=tool_version_id,
        reviewer_id=user.id,
        reason=payload.reason,
    )


@router.post("/{server_id}/tools/{tool_version_id}/reject", response_model=ListingOut)
async def reject_external_tool(
    server_id: str,
    tool_version_id: str,
    payload: ReviewDecision,
    _reviewer: AdminUser,
    db: DB,
    user: CurrentUser,
):
    return await marketplace_service.reject_listing(
        db,
        server_id=server_id,
        tool_version_id=tool_version_id,
        reviewer_id=user.id,
        reason=payload.reason,
    )
