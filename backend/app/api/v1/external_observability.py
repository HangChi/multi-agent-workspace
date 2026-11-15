"""Admin-only diagnostic counters for external MCP operations."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import CurrentUser, require_role
from app.services.external_observability import observability, recent_alerts

router = APIRouter(prefix="/external-tools/observability", tags=["external-tools"])
AdminUser = Annotated[object, Depends(require_role("admin"))]


@router.get("")
async def get_external_observability(
    _user: CurrentUser,
    _admin: AdminUser,
    minutes: int = Query(default=60, ge=1, le=1440),
):
    return {
        **observability.snapshot(),
        "alerts": recent_alerts(minutes=minutes),
    }
