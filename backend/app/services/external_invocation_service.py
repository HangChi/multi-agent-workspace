"""Persistence and safe projections for external MCP invocations."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, load_only

from app.core.config import settings
from app.core.secrets import CredentialCipher, EncryptedCredential
from app.executors.external_tool import ExternalToolExecutor
from app.executors.mcp_security import validate_endpoint
from app.models.external_credential import ExternalCredential
from app.models.external_invocation import ExternalToolInvocation
from app.models.external_tool import (
    ExternalToolGrant,
    ExternalToolServer,
    ExternalToolVersion,
    MarketplaceListing,
    RunExternalToolSnapshot,
    ToolInstallation,
)
from app.services import external_audit_service, external_concurrency
from app.services.external_observability import observe_invocation

_SAFE_ERROR_MESSAGES = {
    "parameter_error": "工具参数校验失败",
    "policy_denied": "外部工具策略拒绝了本次调用",
    "transport_error": "外部工具服务器暂不可用",
    "auth_error": "外部工具服务器认证失败",
    "timeout": "外部工具调用超时",
    "tool_error": "外部工具执行失败",
    "result_truncated": "外部工具结果超过安全上限",
    "cancelled": "外部工具调用已取消",
}


def safe_error_message(category: str) -> str:
    return _SAFE_ERROR_MESSAGES.get(category, "外部工具调用失败")


async def record_invocation(
    db: AsyncSession,
    *,
    run_id: str,
    owner_id: str,
    agent_id: str | None = None,
    server_id: str,
    tool_version_id: str,
    grant_id: str | None,
    credential_id: str | None,
    call_id: str,
    step_id: str | None = None,
    status: str,
    duration_ms: int,
    arguments: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
    policy_result: dict[str, Any] | None = None,
    error_category: str | None = None,
) -> ExternalToolInvocation:
    input_summary = {"keys": sorted(arguments)} if arguments is not None else None
    output_summary = None
    if result is not None:
        output_summary = {
            "keys": sorted(result),
            "result_truncated": bool(result.get("result_truncated")),
        }
    invocation = ExternalToolInvocation(
        run_id=run_id,
        step_id=step_id,
        owner_id=owner_id,
        agent_id=agent_id,
        server_id=server_id,
        tool_version_id=tool_version_id,
        grant_id=grant_id,
        credential_id=credential_id,
        call_id=call_id,
        status=status,
        duration_ms=max(0, duration_ms),
        input_summary=input_summary,
        output_summary=output_summary,
        policy_result=policy_result or {},
        error_summary=safe_error_message(error_category) if error_category else None,
        completed_at=datetime.now(UTC),
    )
    db.add(invocation)
    await db.flush()
    observe_invocation(status)
    await external_audit_service.write_external_audit(
        db,
        user_id=owner_id,
        action="external_tool_invocation",
        target_type="run",
        target_id=run_id,
        detail={
            "invocation_id": invocation.id,
            "agent_id": agent_id,
            "server_id": server_id,
            "tool_version_id": tool_version_id,
            "call_id": call_id,
            "status": status,
            "duration_ms": max(0, duration_ms),
            "error_category": error_category,
            "input_keys": input_summary["keys"] if input_summary else [],
            "output_keys": output_summary["keys"] if output_summary else [],
        },
    )
    return invocation


async def build_external_executors(
    db: AsyncSession,
    *,
    run_id: str,
    owner_id: str,
    abort_check=None,
) -> dict[str, ExternalToolExecutor]:
    """Build one executor per immutable snapshot and fail closed at call time."""
    rows = (
        await db.execute(
            select(
                RunExternalToolSnapshot,
                ExternalToolServer,
                ExternalToolVersion,
                ExternalToolGrant,
                ToolInstallation,
                MarketplaceListing,
                ExternalCredential,
            )
            .join(ExternalToolServer, ExternalToolServer.id == RunExternalToolSnapshot.server_id)
            .join(ExternalToolVersion, ExternalToolVersion.id == RunExternalToolSnapshot.tool_version_id)
            .join(ExternalToolGrant, ExternalToolGrant.id == RunExternalToolSnapshot.grant_id)
            .join(ToolInstallation, ToolInstallation.id == ExternalToolGrant.installation_id)
            .join(MarketplaceListing, MarketplaceListing.id == ToolInstallation.listing_id)
            .outerjoin(ExternalCredential, ExternalCredential.id == RunExternalToolSnapshot.credential_id)
            .where(
                RunExternalToolSnapshot.run_id == run_id,
                RunExternalToolSnapshot.grant_id == ExternalToolGrant.id,
                ToolInstallation.owner_id == owner_id,
            )
        )
    ).all()
    executors: dict[str, ExternalToolExecutor] = {}
    for snapshot, server, version, grant, installation, listing, credential in rows:
        policy_error: str | None = None
        if (
            server.status != "published"
            or version.availability != "valid"
            or grant.status != "active"
            or installation.status != "installed"
            or listing.status != "published"
        ):
            policy_error = "current_state_unavailable"
        if not set(snapshot.permission_snapshot or []).issubset(set(grant.permission_snapshot or [])):
            policy_error = "permissions_changed"
        if server.auth_mode != "none" and credential is None:
            policy_error = "credential_unavailable"
        headers: dict[str, str] = {}
        if credential is not None:
            if credential.status != "active" or credential.server_id != server.id or credential.scope_owner_id != owner_id:
                policy_error = "credential_unavailable"
            else:
                try:
                    value = CredentialCipher(settings.mcp_credential_key).decrypt(
                        EncryptedCredential(credential.ciphertext, credential.key_version)
                    )
                    headers[credential.header_name] = (
                        f"Bearer {value}" if credential.kind == "bearer" else value
                    )
                except ValueError:
                    policy_error = "credential_unavailable"
        try:
            endpoint = validate_endpoint(server.normalized_endpoint_url).url
        except ValueError:
            policy_error = "endpoint_unavailable"
            endpoint = server.normalized_endpoint_url

        async def authorize_current(
            *,
            server_id: str = server.id,
            version_id: str = version.id,
            grant_id: str = grant.id,
            installation_id: str = installation.id,
            listing_id: str = listing.id,
            credential_id: str | None = credential.id if credential else None,
            expected_endpoint: str = endpoint,
            snapshot_permissions: tuple[str, ...] = tuple(snapshot.permission_snapshot or []),
        ) -> bool:
            state = (
                await db.execute(
                    select(
                        ExternalToolServer.status,
                        ExternalToolServer.normalized_endpoint_url,
                        ExternalToolVersion.availability,
                        ExternalToolGrant.status,
                        ExternalToolGrant.permission_snapshot,
                        ToolInstallation.status,
                        MarketplaceListing.status,
                        ToolInstallation.owner_id,
                        ExternalCredential.status,
                    )
                    .select_from(ExternalToolServer)
                    .join(ExternalToolVersion, ExternalToolVersion.server_id == ExternalToolServer.id)
                    .join(ExternalToolGrant, ExternalToolGrant.tool_version_id == ExternalToolVersion.id)
                    .join(ToolInstallation, ToolInstallation.id == ExternalToolGrant.installation_id)
                    .join(MarketplaceListing, MarketplaceListing.id == ToolInstallation.listing_id)
                    .outerjoin(ExternalCredential, ExternalCredential.id == credential_id)
                    .where(
                        ExternalToolServer.id == server_id,
                        ExternalToolVersion.id == version_id,
                        ExternalToolGrant.id == grant_id,
                        ToolInstallation.id == installation_id,
                        MarketplaceListing.id == listing_id,
                    )
                )
            ).first()
            if state is None:
                return False
            (
                server_status,
                current_endpoint,
                availability,
                grant_status,
                current_permissions,
                installation_status,
                listing_status,
                installation_owner,
                credential_status,
            ) = state
            try:
                endpoint_is_safe = validate_endpoint(current_endpoint).url == expected_endpoint
            except ValueError:
                endpoint_is_safe = False
            return (
                endpoint_is_safe
                and owner_id == installation_owner
                and set(snapshot_permissions).issubset(set(current_permissions or []))
                and server_status == "published"
                and availability == "valid"
                and grant_status == "active"
                and installation_status == "installed"
                and listing_status == "published"
                and (credential_id is None or credential_status == "active")
            )

        async def acquire_concurrency_slot(
            endpoint: str,
            *,
            wait_seconds: float,
            abort_check=None,
        ) -> str | None:
            return await external_concurrency.acquire_slot(
                endpoint,
                wait_seconds=wait_seconds,
                abort_check=abort_check,
            )

        async def release_concurrency_slot(endpoint: str, lease: str | None) -> None:
            await asyncio.to_thread(external_concurrency.release_slot, endpoint, lease)

        async def record(
            *,
            server_id: str = server.id,
            tool_version_id: str = version.id,
            grant_id: str = grant.id,
            credential_id: str | None = credential.id if credential else None,
            agent_id: str | None = grant.agent_id,
            **kwargs: Any,
        ) -> None:
            await record_invocation(
                db,
                run_id=run_id,
                owner_id=owner_id,
                agent_id=agent_id,
                server_id=server_id,
                tool_version_id=tool_version_id,
                grant_id=grant_id,
                credential_id=credential_id,
                **kwargs,
            )

        executors[snapshot.stable_name] = ExternalToolExecutor(
            stable_name=snapshot.stable_name,
            external_name=snapshot.external_name,
            version=snapshot.version,
            description=f"{version.description[:800]} (固定版本 {snapshot.version})",
            endpoint=endpoint,
            input_schema=dict(snapshot.schema_snapshot.get("input") or {}),
            permission_snapshot=sorted(
                set(snapshot.permission_snapshot or []) & set(grant.permission_snapshot or [])
            ),
            required_permissions=[],
            headers=headers,
            policy_error=policy_error,
            abort_check=abort_check,
            invocation_recorder=record,
            authorize_call=authorize_current,
            acquire_concurrency_slot=acquire_concurrency_slot,
            release_concurrency_slot=release_concurrency_slot,
        )
    return executors


async def _snapshot_active_grants(
    db: AsyncSession,
    *,
    run_id: str,
    owner_id: str,
    agent_id: str,
) -> None:
    grants = (
        await db.execute(
            select(ExternalToolGrant)
            .options(joinedload(ExternalToolGrant.tool_version))
            .join(ExternalToolGrant.installation)
            .join(ExternalToolGrant.tool_version)
            .join(ExternalToolVersion.server)
            .join(MarketplaceListing, MarketplaceListing.tool_version_id == ExternalToolVersion.id)
            .where(
                ExternalToolGrant.agent_id == agent_id,
                ExternalToolGrant.status == "active",
                ToolInstallation.owner_id == owner_id,
                ToolInstallation.status == "installed",
                MarketplaceListing.status == "published",
                ExternalToolVersion.availability == "valid",
                ExternalToolServer.status == "published",
            )
        )
    ).scalars().all()
    for grant in grants:
        version = grant.tool_version
        db.add(
            RunExternalToolSnapshot(
                run_id=run_id,
                grant_id=grant.id,
                server_id=version.server_id,
                tool_version_id=grant.tool_version_id,
                stable_name=version.stable_name,
                external_name=version.external_name,
                version=version.version,
                permission_snapshot=list(grant.permission_snapshot or []),
                schema_snapshot=dict(grant.schema_snapshot),
                credential_id=grant.credential_id,
            )
        )


async def ensure_external_snapshots(
    db: AsyncSession,
    *,
    run_id: str,
    owner_id: str,
    agent_id: str,
) -> None:
    """Capture router-selected Agent grants exactly once at execution start."""
    existing = await db.scalar(
        select(RunExternalToolSnapshot.id)
        .where(RunExternalToolSnapshot.run_id == run_id)
        .limit(1)
    )
    if existing is None:
        await _snapshot_active_grants(
            db, run_id=run_id, owner_id=owner_id, agent_id=agent_id
        )
    await db.flush()


async def snapshot_external_grants(
    db: AsyncSession,
    *,
    run_id: str,
    owner_id: str,
    agent_id: str,
) -> None:
    """Freeze active external grants for a newly assigned Agent run."""
    await _snapshot_active_grants(
        db, run_id=run_id, owner_id=owner_id, agent_id=agent_id
    )


def invocation_projection(invocation: ExternalToolInvocation) -> dict[str, Any]:
    version = invocation.tool_version
    server = invocation.server
    return {
        "id": invocation.id,
        "step_id": invocation.step_id,
        "server_name": server.name,
        "tool_name": version.external_name,
        "stable_name": version.stable_name,
        "version": version.version,
        "status": invocation.status,
        "duration_ms": invocation.duration_ms,
        "error_category": invocation.status if invocation.status != "succeeded" else None,
        "result_truncated": invocation.status == "result_truncated",
        "created_at": invocation.created_at,
    }


async def list_run_invocations(db: AsyncSession, *, run_id: str) -> list[dict[str, Any]]:
    rows = (
        await db.execute(
            select(ExternalToolInvocation)
            .options(
                # Polling needs metadata, not result JSON, server endpoints or
                # tool schemas. Raise if the projection accidentally grows I/O.
                load_only(
                    ExternalToolInvocation.id, ExternalToolInvocation.step_id,
                    ExternalToolInvocation.status, ExternalToolInvocation.duration_ms,
                    ExternalToolInvocation.created_at, raiseload=True,
                ),
                joinedload(ExternalToolInvocation.tool_version).load_only(
                    ExternalToolVersion.external_name, ExternalToolVersion.stable_name,
                    ExternalToolVersion.version, raiseload=True,
                ),
                joinedload(ExternalToolInvocation.server).load_only(
                    ExternalToolServer.name, raiseload=True,
                ),
            )
            .where(ExternalToolInvocation.run_id == run_id)
            .order_by(ExternalToolInvocation.created_at.asc())
        )
    ).scalars().all()
    return [invocation_projection(row) for row in rows]


async def list_invocation_audit(
    db: AsyncSession,
    *,
    owner_id: str | None = None,
    run_id: str | None = None,
    server_id: str | None = None,
    tool_version_id: str | None = None,
    status: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[dict[str, Any]], int]:
    filters = []
    if owner_id:
        filters.append(ExternalToolInvocation.owner_id == owner_id)
    if run_id:
        filters.append(ExternalToolInvocation.run_id == run_id)
    if server_id:
        filters.append(ExternalToolInvocation.server_id == server_id)
    if tool_version_id:
        filters.append(ExternalToolInvocation.tool_version_id == tool_version_id)
    if status:
        filters.append(ExternalToolInvocation.status == status)
    total = int(
        (
            await db.execute(
                select(func.count()).select_from(ExternalToolInvocation).where(*filters)
            )
        ).scalar_one()
    )
    rows = (
        await db.execute(
            select(ExternalToolInvocation)
            .options(
                joinedload(ExternalToolInvocation.server),
                joinedload(ExternalToolInvocation.tool_version),
            )
            .where(*filters)
            .order_by(ExternalToolInvocation.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()
    return [
        {
            "id": row.id,
            "run_id": row.run_id,
            "owner_id": row.owner_id,
            "agent_id": row.agent_id,
            "server_id": row.server_id,
            "tool_version_id": row.tool_version_id,
            "call_id": row.call_id,
            "status": row.status,
            "duration_ms": row.duration_ms,
            "policy_result": dict(row.policy_result or {}),
            "error_summary": row.error_summary,
            "created_at": row.created_at.isoformat(),
            "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        }
        for row in rows
    ], total
