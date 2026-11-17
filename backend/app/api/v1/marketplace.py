"""Published marketplace browse and reviewer governance endpoints."""

from fastapi import APIRouter, Query

from app.api.deps import DB, AdminUser, CurrentUser, MemberUser
from app.schemas.external_tool import (
    InstallationUpgrade,
    ListingList,
    ListingOut,
    ReviewDecision,
    ToolInstallationOut,
    ToolInstallCreate,
)
from app.services import external_grant_service, marketplace_service

router = APIRouter(prefix="/marketplace", tags=["marketplace"])


@router.get("/tools", response_model=ListingList)
async def browse_marketplace(
    _user: CurrentUser,
    db: DB,
    q: str | None = Query(default=None, max_length=100),
    category: str | None = Query(default=None, max_length=100),
    capability: str | None = Query(default=None, max_length=100),
    maintainer: str | None = Query(default=None, max_length=100),
    version: str | None = Query(default=None, max_length=100),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
):
    items, total = await marketplace_service.browse_listings(
        db,
        q=q,
        category=category,
        capability=capability,
        maintainer=maintainer,
        version=version,
        page=page,
        page_size=page_size,
    )
    return {"items": items, "total": total}


@router.post(
    "/installations/{installation_id}/upgrade", response_model=ToolInstallationOut
)
async def upgrade_marketplace_installation(
    installation_id: str,
    payload: InstallationUpgrade,
    user: CurrentUser,
    _member: MemberUser,
    db: DB,
):
    installation = await external_grant_service.upgrade_installation(
        db,
        installation_id=installation_id,
        new_listing_id=payload.listing_id,
        user_id=user.id,
        reason=payload.reason,
    )
    await db.commit()
    return external_grant_service.installation_projection(installation)


@router.get("/installations")
async def get_installations(_member: MemberUser, user: CurrentUser, db: DB):
    items = await external_grant_service.list_installations(db, owner_id=user.id)
    return {"items": items, "total": len(items)}


@router.post(
    "/tools/{listing_id}/install", response_model=ToolInstallationOut, status_code=201
)
async def install_tool(
    listing_id: str,
    payload: ToolInstallCreate,
    user: CurrentUser,
    _member: MemberUser,
    db: DB,
):
    installation = await external_grant_service.install_listing(
        db,
        listing_id=listing_id,
        owner_id=user.id,
        installed_by=user.id,
        version_policy=payload.version_policy,
    )
    await db.commit()
    return external_grant_service.installation_projection(installation)


@router.get("/tools/{listing_id}", response_model=ListingOut)
async def get_marketplace_listing(listing_id: str, _user: CurrentUser, db: DB):
    return await marketplace_service.listing_detail(db, listing_id)


@router.post(
    "/tools/{listing_id}/resume",
    response_model=ListingOut,
)
async def resume_marketplace_listing(
    listing_id: str,
    payload: ReviewDecision,
    _reviewer: AdminUser,
    db: DB,
    user: CurrentUser,
):
    listing = await marketplace_service.transition_listing(
        db,
        listing_id=listing_id,
        reviewer_id=user.id,
        action="resume",
        reason=payload.reason,
    )
    await db.commit()
    return listing


@router.post(
    "/tools/{listing_id}/pause",
    response_model=ListingOut,
)
async def pause_marketplace_listing(
    listing_id: str,
    payload: ReviewDecision,
    _reviewer: AdminUser,
    db: DB,
    user: CurrentUser,
):
    listing = await marketplace_service.transition_listing(
        db,
        listing_id=listing_id,
        reviewer_id=user.id,
        action="pause",
        reason=payload.reason,
    )
    await db.commit()
    return listing


@router.post(
    "/tools/{listing_id}/revoke",
    response_model=ListingOut,
)
async def revoke_marketplace_listing(
    listing_id: str,
    payload: ReviewDecision,
    _reviewer: AdminUser,
    db: DB,
    user: CurrentUser,
):
    listing = await marketplace_service.transition_listing(
        db,
        listing_id=listing_id,
        reviewer_id=user.id,
        action="revoke",
        reason=payload.reason,
    )
    await db.commit()
    return listing
