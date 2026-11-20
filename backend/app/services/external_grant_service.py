"""Owner-scoped marketplace installations and exact-version Agent grants."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.api.errors import AppError
from app.models.external_credential import ExternalCredential
from app.models.external_tool import (
    ExternalToolGrant,
    ExternalToolVersion,
    MarketplaceListing,
    ToolInstallation,
)
from app.services.agent_service import get_agent
from app.services.external_audit_service import write_external_audit


def installation_projection(row: ToolInstallation) -> dict:
    return {
        "id": row.id,
        "listing_id": row.listing_id,
        "owner_id": row.owner_id,
        "status": row.status,
        "version_policy": row.version_policy,
        "installed_at": row.installed_at,
    }


def _grant_projection(
    grant: ExternalToolGrant, *, unavailable_reason: str | None = None
) -> dict:
    version = grant.tool_version
    credential = grant.credential
    return {
        "id": grant.id,
        "agent_id": grant.agent_id,
        "installation_id": grant.installation_id,
        "tool_version_id": grant.tool_version_id,
        "stable_name": version.stable_name,
        "version": version.version,
        "permissions": list(grant.permission_snapshot or []),
        "credential": (
            {
                "id": credential.id,
                "label": credential.label,
                "kind": credential.kind,
                "header_name": credential.header_name,
                "status": credential.status,
                "key_version": credential.key_version,
                "last_used_at": credential.last_used_at,
            }
            if credential
            else None
        ),
        "status": grant.status,
        "unavailable_reason": unavailable_reason,
    }


async def install_listing(
    db: AsyncSession,
    *,
    listing_id: str,
    owner_id: str,
    installed_by: str,
    version_policy: str,
) -> ToolInstallation:
    listing = await db.scalar(
        select(MarketplaceListing)
        .options(joinedload(MarketplaceListing.tool_version))
        .where(MarketplaceListing.id == listing_id)
    )
    if (
        listing is None
        or listing.status != "published"
        or listing.tool_version.availability != "valid"
    ):
        raise AppError("NOT_FOUND", "已发布且有效的工具 listing 不存在")
    existing = await db.scalar(
        select(ToolInstallation).where(
            ToolInstallation.owner_id == owner_id,
            ToolInstallation.listing_id == listing_id,
            ToolInstallation.status == "installed",
        )
    )
    if existing:
        return existing
    installation = ToolInstallation(
        listing_id=listing_id,
        owner_id=owner_id,
        installed_by=installed_by,
        status="installed",
        version_policy=version_policy,
    )
    db.add(installation)
    await db.flush()
    await write_external_audit(
        db,
        user_id=installed_by,
        action="external_tool_install",
        target_type="tool_installation",
        target_id=installation.id,
        detail={"listing_id": listing_id, "version_policy": version_policy},
    )
    await db.flush()
    await db.refresh(installation)
    return installation


async def list_installations(db: AsyncSession, *, owner_id: str) -> list[dict]:
    rows = (
        (
            await db.execute(
                select(ToolInstallation)
                .where(ToolInstallation.owner_id == owner_id)
                .order_by(ToolInstallation.installed_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [installation_projection(row) for row in rows]


async def create_grant(
    db: AsyncSession,
    *,
    agent_id: str,
    user_id: str,
    installation_id: str,
    tool_version_id: str,
    credential_id: str | None,
    permissions: list[str],
) -> ExternalToolGrant:
    await get_agent(db, agent_id, user_id)
    installation = await db.scalar(
        select(ToolInstallation)
        .options(
            joinedload(ToolInstallation.listing).joinedload(
                MarketplaceListing.tool_version
            )
        )
        .where(ToolInstallation.id == installation_id)
    )
    if installation is None or installation.owner_id != user_id:
        # 016：他人的安装与「不存在」同响应（此前是 403，可被用来枚举安装 id）
        raise AppError("NOT_FOUND", "工具安装不存在")
    listing = installation.listing
    version = await db.scalar(
        select(ExternalToolVersion).where(ExternalToolVersion.id == tool_version_id)
    )
    if (
        installation.status != "installed"
        or listing.status != "published"
        or version is None
        or version.id != listing.tool_version_id
        or version.availability != "valid"
    ):
        raise AppError("CONFLICT", "安装或工具版本不可用")
    allowed = set(version.required_permissions or [])
    requested = set(permissions)
    if len(requested) != len(permissions) or not requested.issubset(allowed):
        raise AppError("INVALID_INPUT", "授权权限必须是工具声明权限的子集")
    credential = None
    if credential_id:
        credential = await db.scalar(
            select(ExternalCredential).where(
                ExternalCredential.id == credential_id,
                ExternalCredential.server_id == version.server_id,
                ExternalCredential.scope_owner_id == user_id,
                ExternalCredential.status == "active",
            )
        )
        if credential is None:
            # 016：查询本身已按 scope_owner_id 过滤，所以「不属于你」与「不存在」
            # 在这里就是同一件事，响应用 404 而不是 403。
            raise AppError("NOT_FOUND", "凭证不存在")
    existing = await db.scalar(
        select(ExternalToolGrant).where(
            ExternalToolGrant.agent_id == agent_id,
            ExternalToolGrant.tool_version_id == tool_version_id,
            ExternalToolGrant.status == "active",
        )
    )
    if existing:
        raise AppError("CONFLICT", "Agent 已拥有该版本的活动授权")
    grant = ExternalToolGrant(
        agent_id=agent_id,
        installation_id=installation.id,
        tool_version_id=version.id,
        credential_id=credential_id,
        permission_snapshot=sorted(requested),
        schema_snapshot={
            "input": version.input_schema,
            "output": version.output_schema,
        },
        status="active",
        granted_by=user_id,
    )
    db.add(grant)
    await db.flush()
    await write_external_audit(
        db,
        user_id=user_id,
        action="external_tool_grant",
        target_type="external_tool_grant",
        target_id=grant.id,
        detail={
            "agent_id": agent_id,
            "tool_version_id": version.id,
            "permissions": sorted(requested),
        },
    )
    await db.flush()
    return await get_grant(db, grant_id=grant.id)


async def get_grant(db: AsyncSession, *, grant_id: str) -> ExternalToolGrant:
    grant = await db.scalar(
        select(ExternalToolGrant)
        .options(
            joinedload(ExternalToolGrant.tool_version),
            joinedload(ExternalToolGrant.credential),
            joinedload(ExternalToolGrant.installation),
        )
        .where(ExternalToolGrant.id == grant_id)
    )
    if grant is None:
        raise AppError("NOT_FOUND", "外部工具授权不存在")
    return grant


async def list_agent_grants(
    db: AsyncSession, *, agent_id: str, user_id: str
) -> list[dict]:
    await get_agent(db, agent_id, user_id)
    rows = (
        (
            await db.execute(
                select(ExternalToolGrant)
                .options(
                    joinedload(ExternalToolGrant.tool_version),
                    joinedload(ExternalToolGrant.credential),
                    joinedload(ExternalToolGrant.installation),
                )
                .where(ExternalToolGrant.agent_id == agent_id)
                .order_by(ExternalToolGrant.granted_at.desc())
            )
        )
        .scalars()
        .all()
    )
    result = []
    for grant in rows:
        reason = None
        if grant.installation.status != "installed":
            reason = "installation_unavailable"
        elif grant.tool_version.availability != "valid":
            reason = "version_unavailable"
        elif grant.credential_id and (
            grant.credential is None or grant.credential.status != "active"
        ):
            reason = "credential_unavailable"
        result.append(_grant_projection(grant, unavailable_reason=reason))
    return result


async def revoke_grant(
    db: AsyncSession, *, agent_id: str, grant_id: str, user_id: str
) -> None:
    await get_agent(db, agent_id, user_id)
    grant = await db.scalar(
        select(ExternalToolGrant).where(
            ExternalToolGrant.id == grant_id,
            ExternalToolGrant.agent_id == agent_id,
        )
    )
    if grant is None:
        raise AppError("NOT_FOUND", "外部工具授权不存在")
    if grant.status == "revoked":
        return
    grant.status = "revoked"
    await write_external_audit(
        db,
        user_id=user_id,
        action="external_tool_grant_revoke",
        target_type="external_tool_grant",
        target_id=grant.id,
        detail={"agent_id": agent_id},
    )
    await db.flush()


async def upgrade_installation(
    db: AsyncSession,
    *,
    installation_id: str,
    new_listing_id: str,
    user_id: str,
    reason: str,
) -> ToolInstallation:
    """Explicitly move an installation and its active grants to a published version."""
    if len(reason.strip()) < 5:
        raise AppError("INVALID_INPUT", "升级说明至少需要 5 个字符")
    installation = await db.scalar(
        select(ToolInstallation)
        .options(
            joinedload(ToolInstallation.listing).joinedload(
                MarketplaceListing.tool_version
            )
        )
        .where(ToolInstallation.id == installation_id)
    )
    if installation is None:
        raise AppError("NOT_FOUND", "工具安装不存在")
    if installation.owner_id != user_id:
        # 016：与上面「installation is None」走同一响应，避免 403/404 差异可枚举
        raise AppError("NOT_FOUND", "工具安装不存在")
    if installation.status != "installed":
        raise AppError("CONFLICT", "只有活动安装可以升级")
    new_listing = await db.scalar(
        select(MarketplaceListing)
        .options(joinedload(MarketplaceListing.tool_version))
        .where(MarketplaceListing.id == new_listing_id)
    )
    if (
        new_listing is None
        or new_listing.status != "published"
        or new_listing.tool_version.availability != "valid"
    ):
        raise AppError("CONFLICT", "升级目标必须是已发布且有效的工具版本")
    current_version = installation.listing.tool_version
    target_version = new_listing.tool_version
    if (
        current_version.server_id != target_version.server_id
        or current_version.external_name != target_version.external_name
    ):
        raise AppError("INVALID_INPUT", "升级目标必须是同一服务器的同名工具")
    if current_version.id == target_version.id:
        raise AppError("CONFLICT", "工具安装已经是该版本")

    grants = (
        (
            await db.execute(
                select(ExternalToolGrant).where(
                    ExternalToolGrant.installation_id == installation.id,
                    ExternalToolGrant.status == "active",
                )
            )
        )
        .scalars()
        .all()
    )
    target_permissions = set(target_version.required_permissions or [])
    for grant in grants:
        if not target_permissions.issubset(set(grant.permission_snapshot or [])):
            raise AppError("CONFLICT", "新版本需要重新确认权限后才能升级")
        grant.tool_version_id = target_version.id
        grant.schema_snapshot = {
            "input": target_version.input_schema,
            "output": target_version.output_schema,
        }
    installation.listing_id = new_listing.id
    await write_external_audit(
        db,
        user_id=user_id,
        action="external_tool_version_upgrade",
        target_type="tool_installation",
        target_id=installation.id,
        detail={
            "from_version": current_version.version,
            "to_version": target_version.version,
            "listing_id": new_listing.id,
            "reason": reason.strip(),
        },
    )
    await db.flush()
    await db.refresh(installation)
    return installation
