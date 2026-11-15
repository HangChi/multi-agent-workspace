"""Bounded, auditable MCP server validation and immutable tool discovery."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.executors import mcp_security
from app.executors.mcp_client import MCPClient, MCPError, MCPTool
from app.models.external_credential import ExternalCredential
from app.models.external_tool import ExternalToolServer, ExternalToolVersion
from app.models.external_validation import ServerValidation
from app.services.external_audit_service import write_external_audit
from app.services.external_credential_service import credential_cipher
from app.services.external_observability import observe_validation


def _safe_error(exc: Exception) -> tuple[str, str]:
    text = str(exc).lower()
    if "401" in text or "403" in text or "auth" in text or "credential" in text:
        return "auth_error", "MCP server authentication rejected"
    if isinstance(exc, (TimeoutError, httpx.TimeoutException)) or "timeout" in text:
        return "timeout", "MCP server validation timed out"
    if isinstance(exc, (MCPError, httpx.HTTPError)):
        return "transport_error", "MCP server connection or protocol failed"
    return "validation_error", "MCP server validation failed"


def _valid_tool(tool: Any) -> bool:
    return (
        isinstance(tool, MCPTool)
        and bool(tool.name)
        and len(tool.name) <= 150
        and isinstance(tool.description, str)
        and isinstance(tool.input_schema, dict)
        and tool.input_schema.get("type") == "object"
    )


def _tool_digest(tools: list[MCPTool]) -> str:
    definitions = [
        {"name": item.name, "description": item.description, "input_schema": item.input_schema, "output_schema": item.output_schema}
        for item in tools
    ]
    return hashlib.sha256(json.dumps(definitions, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def _stable_name(server: ExternalToolServer, external_name: str, definition_hash: str) -> str:
    prefix = re.sub(r"[^a-z0-9]+", "_", server.name.lower()).strip("_")[:40] or "server"
    suffix = re.sub(r"[^a-zA-Z0-9_]+", "_", external_name).strip("_").lower()[:80] or "tool"
    return f"mcp_{prefix}_{suffix}_{definition_hash[:12]}"[:200]


async def validate_server(
    db: AsyncSession,
    *,
    server: ExternalToolServer,
    requested_by: str,
    client_factory: Callable[..., Any] = MCPClient,
) -> ServerValidation:
    started = datetime.now(UTC)
    record = ServerValidation(
        server_id=server.id,
        requested_by=requested_by,
        status="failed",
        connection_status="pending",
        auth_status="pending",
        discovery_status="pending",
        discovered_count=0,
        valid_count=0,
        started_at=started,
    )
    db.add(record)
    await db.flush()
    discovered: list[Any] = []
    had_prior_digest = bool(server.last_validated_at)
    previous_digest = await db.scalar(
        select(ServerValidation.definition_digest)
        .where(
            ServerValidation.server_id == server.id,
            ServerValidation.status == "passed",
            ServerValidation.definition_digest.is_not(None),
        )
        .order_by(ServerValidation.completed_at.desc())
        .limit(1)
    )
    drift_detected = False
    try:
        endpoint = mcp_security.validate_endpoint(server.endpoint_url)
        headers: dict[str, str] = {}
        if server.auth_mode != "none":
            credential = (await db.execute(select(ExternalCredential).where(
                ExternalCredential.server_id == server.id,
                ExternalCredential.scope_owner_id == server.owner_id,
                ExternalCredential.status == "active",
            ).order_by(ExternalCredential.created_at.desc()))).scalars().first()
            if credential is None:
                raise ValueError("credential unavailable")
            if credential.key_version != "v1":
                raise ValueError("credential key version unavailable")
            from app.core.secrets import EncryptedCredential
            secret = credential_cipher().decrypt(EncryptedCredential(credential.ciphertext, credential.key_version))
            value = f"Bearer {secret}" if credential.kind == "bearer" else secret
            headers[credential.header_name] = value
        async with client_factory(endpoint.url, headers=headers) as client:
            await client.initialize()
            record.connection_status = "passed"
            record.auth_status = "passed"
            discovered = await client.list_tools()
        record.discovered_count = len(discovered)
        valid = [tool for tool in discovered if _valid_tool(tool)]
        record.valid_count = len(valid)
        record.discovery_status = "passed" if len(valid) == len(discovered) else "partial"
        record.definition_digest = _tool_digest(valid)
        drift_detected = bool(
            had_prior_digest
            and previous_digest
            and record.definition_digest != previous_digest
        )
        if drift_detected:
            record.safe_error = "MCP tool definitions changed; reviewer approval is required"
        for tool in valid:
            definition_hash = hashlib.sha256(
                json.dumps(
                    [tool.name, tool.description, tool.input_schema, tool.output_schema],
                    sort_keys=True,
                    default=str,
                ).encode()
            ).hexdigest()
            version = f"1.0.0+{definition_hash[:12]}"
            existing = await db.scalar(
                select(ExternalToolVersion.id).where(
                    ExternalToolVersion.server_id == server.id,
                    ExternalToolVersion.external_name == tool.name,
                    ExternalToolVersion.version == version,
                )
            )
            if existing:
                continue
            db.add(
                ExternalToolVersion(
                    server_id=server.id,
                    external_name=tool.name,
                    stable_name=_stable_name(server, tool.name, definition_hash),
                    version=version,
                    description=tool.description[:4000],
                    input_schema=tool.input_schema,
                    output_schema=tool.output_schema,
                    required_permissions=[],
                    definition_hash=definition_hash,
                    availability="valid",
                )
            )
        record.status = "passed" if len(valid) == len(discovered) else "partial"
        if drift_detected:
            versions = (
                await db.execute(
                    select(ExternalToolVersion).where(
                        ExternalToolVersion.server_id == server.id,
                        ExternalToolVersion.availability == "valid",
                    )
                )
            ).scalars().all()
            for old_version in versions:
                old_version.availability = "stale"
            record.status = "partial"
        server.status = "pending_review" if valid else "validation_failed"
    except Exception as exc:  # noqa: BLE001 - validation must fail closed and persist a safe result
        category, safe_message = _safe_error(exc)
        record.status = "failed"
        record.safe_error = safe_message
        if category == "auth_error":
            record.connection_status = "passed"
            record.auth_status = "failed"
        elif category == "timeout":
            record.connection_status = "failed"
            record.auth_status = "not_checked"
        else:
            record.connection_status = "failed"
            record.auth_status = "not_checked"
        record.discovery_status = "not_checked"
        server.status = "validation_failed"
    observe_validation(drift=drift_detected)
    record.completed_at = datetime.now(UTC)
    server.last_validated_at = record.completed_at
    server.current_validation_status = record.status
    await write_external_audit(
        db,
        user_id=requested_by,
        action="external_server_validate",
        target_type="external_tool_server",
        target_id=server.id,
        detail={
            "validation_id": record.id,
            "status": record.status,
            "discovered_count": record.discovered_count,
            "valid_count": record.valid_count,
            "error": record.safe_error,
        },
    )
    await db.commit()
    await db.refresh(record)
    return record
