"""Governed marketplace listings and reviewer state transitions."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.api.errors import AppError
from app.models.external_tool import (
    ExternalToolServer,
    ExternalToolVersion,
    MarketplaceListing,
)
from app.services.external_audit_service import write_external_audit


def listing_projection(listing: MarketplaceListing) -> dict:
    version = listing.tool_version
    server = version.server
    return {
        "listing_id": listing.id,
        "tool_version_id": version.id,
        "stable_name": version.stable_name,
        "display_name": f"{server.name} / {version.external_name}",
        "description": version.description[:4000],
        "server_name": server.name,
        "version": version.version,
        "permissions": list(version.required_permissions or []),
        "restrictions": dict(listing.restrictions or {}),
        "status": listing.status,
        "validated_at": server.last_validated_at,
    }


async def _refresh_server_status(db: AsyncSession, server: ExternalToolServer) -> None:
    """Advance the server's coarse status to match its published listings.

    Registration leaves a server ``pending_review`` and validation only confirms
    or rejects it, so nothing else used to move it: a server whose tool a reviewer
    published stayed ``pending_review`` forever. Grant snapshotting requires a
    published server, so no external tool could ever be called, and resuming a
    paused listing requires the same, so a pause was permanent.

    A server with at least one published listing is ``published``. Revoking the
    last one returns it to ``pending_review``. Pausing a listing deliberately
    leaves the server published -- the server itself is still governed and
    usable, while grants fail closed on the listing's own status.
    """
    published = await db.scalar(
        select(func.count())
        .select_from(MarketplaceListing)
        .join(MarketplaceListing.tool_version)
        .where(ExternalToolVersion.server_id == server.id,
               MarketplaceListing.status == "published")
    )
    if published and server.status not in {"rejected", "revoked"}:
        server.status = "published"
    elif not published and server.status == "published":
        server.status = "pending_review"


async def _listing(db: AsyncSession, listing_id: str) -> MarketplaceListing:
    listing = await db.scalar(
        select(MarketplaceListing)
        .options(joinedload(MarketplaceListing.tool_version).joinedload(ExternalToolVersion.server))
        .where(MarketplaceListing.id == listing_id)
    )
    if listing is None:
        raise AppError("NOT_FOUND", "市场 listing 不存在")
    return listing


async def browse_listings(
    db: AsyncSession,
    *,
    q: str | None = None,
    category: str | None = None,
    capability: str | None = None,
    maintainer: str | None = None,
    version: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[dict], int]:
    filters = [MarketplaceListing.status == "published", ExternalToolVersion.availability == "valid"]
    if q:
        term = f"%{q}%"
        filters.append(
            ExternalToolVersion.external_name.ilike(term)
            | ExternalToolVersion.description.ilike(term)
            | ExternalToolServer.name.ilike(term)
        )
    if category:
        filters.append(ExternalToolVersion.capability_category == category)
    if capability:
        filters.append(ExternalToolVersion.required_permissions.contains([capability]))
    if maintainer:
        filters.append(ExternalToolServer.maintainer == maintainer)
    if version:
        filters.append(ExternalToolVersion.version == version)
    query = (
        select(MarketplaceListing)
        .join(MarketplaceListing.tool_version)
        .join(ExternalToolVersion.server)
        .options(joinedload(MarketplaceListing.tool_version).joinedload(ExternalToolVersion.server))
        .where(*filters)
        .order_by(MarketplaceListing.published_at.desc().nullslast())
    )
    rows = (await db.execute(query.offset((page - 1) * page_size).limit(page_size))).scalars().all()
    count_query = (
        select(func.count())
        .select_from(MarketplaceListing)
        .join(MarketplaceListing.tool_version)
        .join(ExternalToolVersion.server)
        .where(*filters)
    )
    total = (await db.execute(count_query)).scalar_one()
    return [listing_projection(row) for row in rows], total


async def listing_detail(db: AsyncSession, listing_id: str) -> dict:
    listing = await _listing(db, listing_id)
    if listing.status != "published" or listing.tool_version.availability != "valid":
        raise AppError("NOT_FOUND", "市场 listing 不可见")
    return listing_projection(listing)


async def _review_listing(
    db: AsyncSession,
    *,
    server_id: str,
    tool_version_id: str,
    reviewer_id: str,
    reason: str,
    status: str,
    audit_action: str,
    require_valid: bool = False,
) -> dict:
    if len(reason.strip()) < 5:
        raise AppError("INVALID_INPUT", "审核说明至少需要 5 个字符")
    version = await db.scalar(
        select(ExternalToolVersion)
        .options(joinedload(ExternalToolVersion.server))
        .where(ExternalToolVersion.id == tool_version_id, ExternalToolVersion.server_id == server_id)
    )
    if version is None:
        raise AppError("NOT_FOUND", "外部工具版本不存在")
    if require_valid and (
        version.server.current_validation_status != "passed" or version.availability != "valid"
    ):
        raise AppError("CONFLICT", "只有通过验证且有效的版本可以发布")
    listing = await db.scalar(
        select(MarketplaceListing)
        .options(joinedload(MarketplaceListing.tool_version).joinedload(ExternalToolVersion.server))
        .where(MarketplaceListing.tool_version_id == version.id)
    )
    if listing is None:
        listing = MarketplaceListing(
            tool_version_id=version.id,
            status=status,
            usage_summary=version.description[:1000],
            data_handling="按服务器声明处理外部请求",
            restrictions={"timeout_seconds": 30} if status == "published" else {},
        )
        db.add(listing)
        await db.flush()
        listing.tool_version = version
    reviewed_at = datetime.now(UTC)
    listing.status = status
    listing.reviewer_id = reviewer_id
    listing.reviewed_at = reviewed_at
    listing.review_reason = reason.strip()
    if status == "published":
        listing.published_at = reviewed_at
        await _refresh_server_status(db, version.server)
    await write_external_audit(
        db,
        user_id=reviewer_id,
        action=audit_action,
        target_type="marketplace_listing",
        target_id=listing.id,
        detail={"tool_version_id": version.id, "reason": reason.strip()},
    )
    await db.commit()
    return listing_projection(await _listing(db, listing.id))


async def approve_listing(
    db: AsyncSession, *, server_id: str, tool_version_id: str, reviewer_id: str, reason: str
) -> dict:
    return await _review_listing(
        db,
        server_id=server_id,
        tool_version_id=tool_version_id,
        reviewer_id=reviewer_id,
        reason=reason,
        status="published",
        audit_action="external_tool_approve",
        require_valid=True,
    )


async def reject_listing(
    db: AsyncSession, *, server_id: str, tool_version_id: str, reviewer_id: str, reason: str
) -> dict:
    return await _review_listing(
        db,
        server_id=server_id,
        tool_version_id=tool_version_id,
        reviewer_id=reviewer_id,
        reason=reason,
        status="rejected",
        audit_action="external_tool_reject",
    )


async def transition_listing(
    db: AsyncSession, *, listing_id: str, reviewer_id: str, action: str, reason: str
) -> dict:
    if len(reason.strip()) < 5:
        raise AppError("INVALID_INPUT", "状态变更说明至少需要 5 个字符")
    listing = await _listing(db, listing_id)
    if action == "pause":
        if listing.status != "published":
            raise AppError("CONFLICT", "只有已发布 listing 可以暂停")
        listing.status = "paused"
        audit_action = "external_tool_pause"
    elif action == "resume":
        if listing.status != "paused":
            raise AppError("CONFLICT", "只有已暂停 listing 可以恢复")
        if listing.tool_version.availability != "valid" or listing.tool_version.server.status != "published":
            raise AppError("CONFLICT", "工具版本或服务器当前不可用")
        listing.status = "published"
        listing.published_at = datetime.now(UTC)
        audit_action = "external_tool_resume"
    elif action == "revoke":
        if listing.status == "revoked":
            raise AppError("CONFLICT", "listing 已撤销")
        listing.status = "revoked"
        listing.tool_version.availability = "revoked"
        audit_action = "external_tool_revoke"
        await _refresh_server_status(db, listing.tool_version.server)
    else:
        raise AppError("INVALID_INPUT", "不支持的 listing 状态变更")
    listing.reviewer_id = reviewer_id
    listing.reviewed_at = datetime.now(UTC)
    listing.review_reason = reason.strip()
    await write_external_audit(
        db,
        user_id=reviewer_id,
        action=audit_action,
        target_type="marketplace_listing",
        target_id=listing.id,
        detail={"reason": reason.strip(), "status": listing.status},
    )
    await db.commit()
    return listing_projection(await _listing(db, listing.id))
