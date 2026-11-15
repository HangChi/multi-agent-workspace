"""统计端点（T078）：GET /stats/overview 仪表盘数据。

对齐 tasks.md T078：
- 所有登录用户（admin/member/viewer）可查看自己的统计
- 数据按用户隔离（owner_id = 当前用户）
- 返回 running/queued/completed_today/failed_today/success_rate_7d/
  cost_today_cents/cost_trend_7d
"""

from fastapi import APIRouter

from app.api.deps import DB, CurrentUser
from app.services import stats_service

router = APIRouter(prefix="/stats", tags=["stats"])


@router.get("/overview")
async def stats_overview(user: CurrentUser, db: DB):
    """仪表盘总览：当前用户的工作负载与成本统计。"""
    return await stats_service.overview(db, user.id, cache=True)
