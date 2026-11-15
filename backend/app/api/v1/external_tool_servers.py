"""Administrator endpoints for external MCP server registration and validation."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import DB, CurrentUser, require_role
from app.schemas.external_tool import (
    ExternalServerCreate,
    ExternalServerList,
    ExternalServerOut,
    ExternalServerUpdate,
)
from app.services import external_tool_service
from app.tasks.validate_external_server import validate_external_server

router = APIRouter(prefix="/external-tool-servers", tags=["external-tools"])
AdminUser = Annotated[object, Depends(require_role("admin"))]


@router.post("", response_model=ExternalServerOut, status_code=status.HTTP_201_CREATED)
async def create_external_server(payload: ExternalServerCreate, user: CurrentUser, _admin: AdminUser, db: DB):
    server = await external_tool_service.create_server(db, owner_id=user.id, created_by=user.id, payload=payload)
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
        db, owner_id=user.id, is_admin=True, status=status_filter, q=q, page=page, page_size=page_size
    )
    return {"items": items, "total": total}


@router.get("/{server_id}", response_model=ExternalServerOut)
async def get_external_server(server_id: str, user: CurrentUser, _admin: AdminUser, db: DB):
    server = await external_tool_service.get_server(db, server_id=server_id, owner_id=user.id, is_admin=True)
    return external_tool_service.server_projection(server)


@router.patch("/{server_id}", response_model=ExternalServerOut)
async def update_external_server(server_id: str, payload: ExternalServerUpdate, user: CurrentUser, _admin: AdminUser, db: DB):
    server = await external_tool_service.get_server(db, server_id=server_id, owner_id=user.id, is_admin=True)
    server = await external_tool_service.update_server(db, server=server, payload=payload)
    return external_tool_service.server_projection(server)


@router.post("/{server_id}/validate", status_code=status.HTTP_202_ACCEPTED)
async def validate_external_server_endpoint(server_id: str, user: CurrentUser, _admin: AdminUser, db: DB):
    await external_tool_service.get_server(db, server_id=server_id, owner_id=user.id, is_admin=True)
    task = validate_external_server.delay(server_id, user.id)
    return {"status": "queued", "task_id": getattr(task, "id", None)}
