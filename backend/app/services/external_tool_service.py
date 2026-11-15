"""Owner-scoped registration and safe projection for external MCP servers."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.errors import AppError
from app.executors.mcp_security import validate_endpoint
from app.models.external_tool import ExternalToolServer, ExternalToolVersion
from app.schemas.external_tool import ExternalServerCreate, ExternalServerUpdate
from app.services.external_audit_service import write_external_audit
from app.services.external_credential_service import create_credential
from app.services.external_observability import observe_destination_change


def _safe_server(server: ExternalToolServer) -> dict:
    validations = sorted(server.validations, key=lambda item: item.started_at, reverse=True)
    last = validations[0] if validations else None
    return {
        "id": server.id,
        "owner_id": server.owner_id,
        "name": server.name,
        "description": server.description,
        "endpoint_url": server.endpoint_url,
        "source_url": server.source_url,
        "maintainer": server.maintainer,
        "auth_mode": server.auth_mode,
        "status": server.status,
        "created_at": server.created_at.isoformat() if server.created_at else datetime.now(UTC).isoformat(),
        "updated_at": server.updated_at.isoformat() if server.updated_at else datetime.now(UTC).isoformat(),
        "last_validated_at": server.last_validated_at.isoformat() if server.last_validated_at else None,
        "current_validation_status": server.current_validation_status,
        "tools": [
            {
                "id": tool.id,
                "external_name": tool.external_name,
                "stable_name": tool.stable_name,
                "version": tool.version,
                "description": tool.description,
                "input_schema": tool.input_schema,
                "output_schema": tool.output_schema,
                "required_permissions": tool.required_permissions,
                "definition_hash": tool.definition_hash,
                "availability": tool.availability,
                "discovered_at": tool.discovered_at,
                "listing_id": tool.listings[0].id if tool.listings else None,
                "listing_status": tool.listings[0].status if tool.listings else None,
            }
            for tool in server.tools
        ],
        "credentials": [
            {
                "id": credential.id,
                "label": credential.label,
                "kind": credential.kind,
                "header_name": credential.header_name,
                "status": credential.status,
                "key_version": credential.key_version,
                "last_used_at": credential.last_used_at.isoformat() if credential.last_used_at else None,
            }
            for credential in server.credentials
        ],
        "last_validation": ({
            "id": last.id,
            "status": last.status,
            "connection_status": last.connection_status,
            "auth_status": last.auth_status,
            "discovery_status": last.discovery_status,
            "discovered_count": last.discovered_count,
            "valid_count": last.valid_count,
            "safe_error": last.safe_error,
            "started_at": last.started_at.isoformat(),
            "completed_at": last.completed_at.isoformat() if last.completed_at else None,
        } if last else None),
    }


async def create_server(
    db: AsyncSession, *, owner_id: str, created_by: str, payload: ExternalServerCreate
) -> ExternalToolServer:
    try:
        endpoint = validate_endpoint(payload.endpoint_url)
    except ValueError as exc:
        raise AppError("INVALID_INPUT", str(exc)) from exc
    existing = await db.scalar(select(ExternalToolServer.id).where(
        ExternalToolServer.owner_id == owner_id,
        ExternalToolServer.normalized_endpoint_url == endpoint.url,
    ))
    if existing:
        raise AppError("CONFLICT", "该外部服务器 endpoint 已登记")
    server = ExternalToolServer(
        owner_id=owner_id,
        created_by=created_by,
        name=payload.name.strip(),
        description=payload.description.strip(),
        endpoint_url=payload.endpoint_url,
        normalized_endpoint_url=endpoint.url,
        source_url=str(payload.source_url) if payload.source_url else None,
        maintainer=payload.maintainer.strip(),
        auth_mode=payload.auth_mode,
        status="pending_review",
    )
    db.add(server)
    try:
        await db.flush()
    except IntegrityError as exc:
        raise AppError("CONFLICT", "该外部服务器 endpoint 已登记") from exc
    if payload.credential:
        await create_credential(
            db,
            server_id=server.id,
            owner_id=owner_id,
            created_by=created_by,
            label=payload.credential.label,
            kind=payload.credential.kind,
            value=payload.credential.value,
            header_name=payload.credential.header_name,
        )
    await write_external_audit(
        db,
        user_id=created_by,
        action="external_server_register",
        target_type="external_tool_server",
        target_id=server.id,
        detail={"name": server.name, "auth_mode": server.auth_mode},
    )
    await db.commit()
    return await get_server(db, server_id=server.id, owner_id=owner_id, is_admin=True)


async def list_servers(
    db: AsyncSession, *, owner_id: str, is_admin: bool, status: str | None, q: str | None, page: int, page_size: int
) -> tuple[list[dict], int]:
    filters = [] if is_admin else [ExternalToolServer.owner_id == owner_id]
    if status:
        filters.append(ExternalToolServer.status == status)
    if q:
        filters.append(ExternalToolServer.name.ilike(f"%{q}%"))
    total = int((await db.execute(select(func.count()).select_from(ExternalToolServer).where(*filters))).scalar_one())
    rows = (await db.execute(
        select(ExternalToolServer).options(
            selectinload(ExternalToolServer.credentials),
            selectinload(ExternalToolServer.validations),
            selectinload(ExternalToolServer.tools).selectinload(ExternalToolVersion.listings),
        ).where(*filters).order_by(ExternalToolServer.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    )).scalars().all()
    return [_safe_server(row) for row in rows], total


async def get_server(
    db: AsyncSession, *, server_id: str, owner_id: str, is_admin: bool = False
) -> ExternalToolServer:
    stmt = select(ExternalToolServer).options(
        selectinload(ExternalToolServer.credentials), selectinload(ExternalToolServer.validations),
        selectinload(ExternalToolServer.tools),
    ).where(ExternalToolServer.id == server_id)
    if not is_admin:
        stmt = stmt.where(ExternalToolServer.owner_id == owner_id)
    server = (await db.execute(stmt)).scalars().first()
    if server is None:
        raise AppError("NOT_FOUND", "外部工具服务器不存在")
    return server


async def update_server(
    db: AsyncSession, *, server: ExternalToolServer, payload: ExternalServerUpdate
) -> ExternalToolServer:
    changes = payload.model_dump(exclude_unset=True)
    if changes.get("endpoint_url"):
        try:
            endpoint = validate_endpoint(changes["endpoint_url"])
        except ValueError as exc:
            raise AppError("INVALID_INPUT", str(exc)) from exc
        duplicate = await db.scalar(select(ExternalToolServer.id).where(
            ExternalToolServer.owner_id == server.owner_id,
            ExternalToolServer.normalized_endpoint_url == endpoint.url,
            ExternalToolServer.id != server.id,
        ))
        if duplicate:
            raise AppError("CONFLICT", "该外部服务器 endpoint 已登记")
        server.endpoint_url = changes["endpoint_url"]
        server.normalized_endpoint_url = endpoint.url
        observe_destination_change()
        server.status = "draft"
        server.current_validation_status = None
    for field in ("name", "description", "source_url", "maintainer"):
        if field in changes:
            setattr(server, field, changes[field])
    server.updated_at = datetime.now(UTC)
    await db.commit()
    return await get_server(db, server_id=server.id, owner_id=server.owner_id, is_admin=True)


def server_projection(server: ExternalToolServer) -> dict:
    return _safe_server(server)
