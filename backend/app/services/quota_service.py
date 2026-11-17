"""用户月配额服务（T077）：月成本累计 + 超月配额熔断（宪法「每个用户有月配额」）。

设计：
- 配额按「已发生成本」结算：月用量 = SUM(runs.cost_cents) WHERE owner_id + 当月 + status != cancelled
  （cancelled 的 Run 不计费；completed/failed/timeout/needs_input 均计入实际消耗）
- 熔断时机（双入口）：
  1. 分配入口（API 侧）：assign 前校验，未超限才建 Run 入队（拒绝返回 429 RATE_LIMITED）
  2. 执行入口（Worker 侧）：execute_run 消费前校验，兜底防越权执行（配额是软约束，
     已入队任务不因配额变化而回滚，但新任务一律拒绝）
- 配额按用户隔离：每个用户独立累计，互不影响
- 月份边界：UTC 自然月（run.created_at 使用 UTC 存储，对齐）
"""

from datetime import UTC, datetime

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.core.config import settings
from app.models.run import Run

logger = structlog.get_logger(__name__)

# 月配额上限（分）：取自配置（MONTHLY_QUOTA_CENTS 可经 env 覆盖）。
# 保留模块级常量作为注入点——测试通过 monkeypatch 覆盖它来构造小额配额。
MONTHLY_QUOTA_CENTS = settings.monthly_quota_cents


def _month_start_utc() -> datetime:
    """当前 UTC 自然月月初（1 日 00:00:00.000000）。"""
    now = datetime.now(UTC)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


class QuotaExceeded(AppError):
    """月配额已用尽：映射 429 RATE_LIMITED（对齐 errors.py 错误码表）。"""

    def __init__(self, usage_cents: int, quota_cents: int):
        self.usage_cents = usage_cents
        self.quota_cents = quota_cents
        super().__init__(
            "RATE_LIMITED",
            f"月配额已用尽（本月已用 {usage_cents} 分 / 上限 {quota_cents} 分），请下月再试或联系管理员",
        )


async def get_month_usage(db: AsyncSession, owner_id: str) -> int:
    """用户本月已发生成本（分）：SUM(runs.cost_cents) WHERE owner + 当月 + 非 cancelled。

    Returns:
        int: 月用量（分），无记录为 0
    """
    stmt = select(func.coalesce(func.sum(Run.cost_cents), 0)).where(
        Run.owner_id == owner_id,
        Run.created_at >= _month_start_utc(),
        Run.status != "cancelled",
    )
    return int((await db.execute(stmt)).scalar_one())


def assert_quota_available(usage_cents: int, quota_cents: int) -> None:
    """熔断校验：用量 >= 上限即抛 QuotaExceeded（配额到达即拒绝新任务）。

    Args:
        usage_cents: 本月已用成本（分）
        quota_cents: 月配额上限（分）

    Raises:
        ValueError: 负数用量（非法数据）
        QuotaExceeded: 达到/超过上限（429）
    """
    if usage_cents < 0:
        raise ValueError(f"非法用量: {usage_cents}")
    if usage_cents >= quota_cents:
        raise QuotaExceeded(usage_cents, quota_cents)


async def assert_user_quota(db: AsyncSession, owner_id: str) -> None:
    """便捷入口：查用量 + 熔断（分配/执行入口共用）。

    Raises:
        QuotaExceeded: 月配额已用尽（429）
    """
    usage = await get_month_usage(db, owner_id)
    logger.info(
        "quota_check",
        user_id=owner_id[:8],
        usage_cents=usage,
        quota_cents=MONTHLY_QUOTA_CENTS,
    )
    assert_quota_available(usage, MONTHLY_QUOTA_CENTS)
