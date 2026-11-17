"""Tool definitions come from code; database rows may only restrict availability."""

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.executors.catalog import CATALOG, ToolSpec
from app.models.agent import Agent
from app.models.tool import Tool


async def enabled_tool_specs(db: AsyncSession, *, lock: bool = False) -> list[ToolSpec]:
    """Intersect implemented names with explicit enabled rows; missing rows deny."""
    stmt = select(Tool.name).where(Tool.enabled.is_(True), Tool.name.in_(CATALOG))
    if lock:
        # Serialize grant writes with operator disables; the disable trigger then
        # removes grants committed before it, rather than losing a concurrent add.
        stmt = stmt.with_for_update(read=True)
    names = set((await db.execute(stmt)).scalars().all())
    return [spec for name, spec in CATALOG.items() if name in names]


def filter_grants(names: Iterable[str] | None, enabled: Iterable[str]) -> list[str]:
    allowed = set(enabled)
    return list(dict.fromkeys(name for name in names or [] if name in allowed))


async def validate_grants(db: AsyncSession, names: list[str] | None) -> None:
    if names is None:
        raise AppError("INVALID_INPUT", "工具集必须为列表；不授权任何工具请传 []")
    enabled = [spec.name for spec in await enabled_tool_specs(db, lock=True)]
    unavailable = set(names) - set(enabled)
    if unavailable:
        raise AppError(
            "INVALID_INPUT",
            f"工具未启用或已下线: {', '.join(sorted(unavailable))}；可选工具: {', '.join(enabled) or '无'}",
        )


def reconcile_agent_grants(agent: Agent, enabled: Iterable[str]) -> list[str]:
    """Repair only the agent being saved/assigned/executed, never scan all agents."""
    grants = filter_grants(agent.tools, enabled)
    if grants != agent.tools:
        agent.tools = grants
    return grants
