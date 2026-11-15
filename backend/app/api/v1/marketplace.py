"""Published marketplace browse and reviewer governance endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import DB, CurrentUser, require_role
from app.schemas.external_tool import ListingList, ListingOut, ReviewDecision
from app.services import marketplace_service

router = APIRouter(prefix="/marketplace", tags=["marketplace"])
Reviewer = Annotated[object, Depends(require_role("admin"))]


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


@router.get("/tools/{listing_id}", response_model=ListingOut)
async def get_marketplace_listing(listing_id: str, _user: CurrentUser, db: DB):
    return await marketplace_service.listing_detail(db, listing_id)


@router.post(
    "/tools/{listing_id}/pause",
    response_model=ListingOut,
)
async def pause_marketplace_listing(listing_id: str, payload: ReviewDecision, _reviewer: Reviewer, db: DB, user: CurrentUser):
    return await marketplace_service.transition_listing(
        db, listing_id=listing_id, reviewer_id=user.id, action="pause", reason=payload.reason
    )


@router.post(
    "/tools/{listing_id}/revoke",
    response_model=ListingOut,
)
async def revoke_marketplace_listing(listing_id: str, payload: ReviewDecision, _reviewer: Reviewer, db: DB, user: CurrentUser):
    return await marketplace_service.transition_listing(
        db, listing_id=listing_id, reviewer_id=user.id, action="revoke", reason=payload.reason
    )
