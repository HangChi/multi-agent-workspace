"""出站规则与访问记录服务（002 US5）。"""

from __future__ import annotations

import ipaddress
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.core.config import settings
from app.executors.egress import RuleView, rule_view_from_row
from app.models.egress import EgressAccess, EgressRule
from app.schemas.egress import BlacklistIn, EgressRuleIn


def _builtin_cidrs() -> list[ipaddress._BaseNetwork]:
    return [ipaddress.ip_network(item) for item in settings.egress_builtin_blacklist_list]


def covered_by_builtin(payload: EgressRuleIn) -> bool:
    """判断自定义规则是否试图放宽内置内网黑名单。"""
    if payload.target_type == "domain":
        return False
    try:
        if payload.target_type == "ip":
            address = ipaddress.ip_address(payload.target)
            return any(address in network for network in _builtin_cidrs())
        network = ipaddress.ip_network(payload.target, strict=True)
        return any(
            network.version == builtin.version
            and network.overlaps(builtin)
            for builtin in _builtin_cidrs()
        )
    except ValueError:
        return False


async def _ensure_agent_owner(db: AsyncSession, agent_id: str, user_id: str, role: str) -> None:
    from app.models.agent import Agent

    agent = await db.get(Agent, agent_id)
    if agent is None:
        raise AppError("NOT_FOUND", "Agent 不存在")
    if role != "admin" and agent.owner_id != user_id:
        raise AppError("FORBIDDEN", "无权操作他人 Agent")


async def list_agent_rules(db: AsyncSession, agent_id: str) -> list[EgressRule]:
    result = await db.execute(
        select(EgressRule)
        .where(EgressRule.scope == "agent", EgressRule.agent_id == agent_id)
        .order_by(EgressRule.created_at.asc())
    )
    return list(result.scalars().all())


async def create_agent_rule(
    db: AsyncSession,
    *,
    agent_id: str,
    user_id: str,
    role: str,
    payload: EgressRuleIn,
) -> EgressRule:
    await _ensure_agent_owner(db, agent_id, user_id, role)
    if covered_by_builtin(payload):
        raise AppError("INVALID_INPUT", "该目标被全局内置黑名单覆盖，不能通过 Agent 规则放行")
    port_filter = (
        EgressRule.port_range.is_(None)
        if payload.port_range is None
        else EgressRule.port_range == payload.port_range
    )
    duplicate = await db.execute(
        select(EgressRule.id).where(
            EgressRule.scope == "agent",
            EgressRule.agent_id == agent_id,
            EgressRule.target_type == payload.target_type,
            EgressRule.target == payload.target,
            port_filter,
        )
    )
    if duplicate.scalar_one_or_none() is not None:
        raise AppError("CONFLICT", "同一 Agent 内已存在相同出站规则")
    rule = EgressRule(
        scope="agent",
        agent_id=agent_id,
        action="allow",
        target_type=payload.target_type,
        target=payload.target,
        port_range=payload.port_range,
        note=payload.note,
        created_by=user_id,
    )
    db.add(rule)
    await db.flush()
    return rule


async def delete_agent_rule(
    db: AsyncSession, *, agent_id: str, rule_id: str, user_id: str, role: str
) -> None:
    await _ensure_agent_owner(db, agent_id, user_id, role)
    rule = await db.get(EgressRule, rule_id)
    if rule is None or rule.scope != "agent" or rule.agent_id != agent_id:
        raise AppError("NOT_FOUND", "出站规则不存在")
    await db.delete(rule)
    await db.flush()


async def list_global_rules(db: AsyncSession) -> list[EgressRule | dict[str, Any]]:
    result = await db.execute(
        select(EgressRule).where(EgressRule.scope == "global").order_by(EgressRule.created_at.asc())
    )
    rows: list[EgressRule | dict[str, Any]] = list(result.scalars().all())
    existing = {row.target for row in rows if isinstance(row, EgressRule)}
    for cidr in settings.egress_builtin_blacklist_list:
        if cidr not in existing:
            rows.insert(
                0,
                {
                    "id": f"builtin:{cidr}",
                    "scope": "global",
                    "agent_id": None,
                    "action": "deny",
                    "target_type": "cidr",
                    "target": cidr,
                    "port_range": None,
                    "note": "内置内网与云元数据保护，不可删除",
                    "enabled": True,
                    "builtin": True,
                    "created_by": None,
                    "created_at": None,
                },
            )
    return rows


async def create_global_rule(
    db: AsyncSession, *, user_id: str, payload: EgressRuleIn | BlacklistIn
) -> EgressRule:
    port_filter = (
        EgressRule.port_range.is_(None)
        if payload.port_range is None
        else EgressRule.port_range == payload.port_range
    )
    duplicate = await db.execute(
        select(EgressRule.id).where(
            EgressRule.scope == "global",
            EgressRule.target_type == payload.target_type,
            EgressRule.target == payload.target,
            port_filter,
        )
    )
    if duplicate.scalar_one_or_none() is not None:
        raise AppError("CONFLICT", "全局黑名单中已存在相同规则")
    rule = EgressRule(
        scope="global",
        action="deny",
        target_type=payload.target_type,
        target=payload.target,
        port_range=payload.port_range,
        note=payload.note,
        created_by=user_id,
    )
    db.add(rule)
    await db.flush()
    return rule


async def delete_global_rule(db: AsyncSession, *, rule_id: str) -> None:
    if rule_id.startswith("builtin:"):
        raise AppError("INVALID_INPUT", "内置黑名单项不可删除")
    rule = await db.get(EgressRule, rule_id)
    if rule is None or rule.scope != "global":
        raise AppError("NOT_FOUND", "全局黑名单规则不存在")
    if rule.target in settings.egress_builtin_blacklist_list:
        raise AppError("INVALID_INPUT", "内置黑名单项不可删除")
    await db.delete(rule)
    await db.flush()


async def snapshot_for_execution(
    db: AsyncSession, *, agent_id: str
) -> tuple[str, list[RuleView], list[RuleView]]:
    """在执行开始时一次性快照 Agent 模式、全局规则和 Agent 规则。"""
    from app.models.agent import Agent

    agent = await db.get(Agent, agent_id)
    if agent is None:
        raise AppError("NOT_FOUND", "Agent 不存在")
    global_rows = await db.execute(
        select(EgressRule).where(EgressRule.scope == "global", EgressRule.enabled.is_(True))
    )
    agent_rows = await db.execute(
        select(EgressRule).where(
            EgressRule.scope == "agent",
            EgressRule.agent_id == agent_id,
            EgressRule.enabled.is_(True),
        )
    )
    return (
        str(agent.egress_mode or "default"),
        [rule_view_from_row(row) for row in global_rows.scalars().all()],
        [rule_view_from_row(row) for row in agent_rows.scalars().all()],
    )


async def record_accesses(
    db: AsyncSession,
    *,
    run_id: str,
    agent_id: str,
    user_id: str,
    accesses: list[dict[str, Any]],
    execution_id: str | None = None,
) -> list[EgressAccess]:
    """批量写入已求值的连接事实；非法目标按 ip-direct 记录但不抛异常。"""
    rows: list[EgressAccess] = []
    for item in accesses:
        target_ip = str(item.get("target_ip", ""))
        try:
            ipaddress.ip_address(target_ip)
        except ValueError:
            continue
        domain = item.get("target_domain") or "ip-direct"
        row = EgressAccess(
            run_id=run_id,
            execution_id=execution_id,
            agent_id=agent_id,
            user_id=user_id,
            target_ip=target_ip,
            target_domain=str(domain),
            port=int(item.get("port", 0)),
            protocol=str(item.get("protocol", "tcp")),
            action=str(item.get("action", "allow")),
            matched_rule_id=item.get("matched_rule_id"),
            bytes_out=item.get("bytes_out"),
            occurred_at=item.get("occurred_at"),
        )
        db.add(row)
        rows.append(row)
    await db.flush()
    return rows


def merge_connection_records(
    dns_queries: list[dict[str, Any]],
    connections: list[dict[str, Any]],
    *,
    window_seconds: float = 5.0,
) -> list[dict[str, Any]]:
    """将 DNS 查询与 conntrack 快照按 IP/时间窗归并为可持久化记录。"""
    merged: list[dict[str, Any]] = []
    for connection in connections:
        ip = str(connection.get("target_ip", ""))
        at = float(connection.get("timestamp", 0.0))
        candidates = [
            query
            for query in dns_queries
            if str(query.get("resolved_ip", "")) == ip
            and abs(float(query.get("timestamp", 0.0)) - at) <= window_seconds
        ]
        nearest = min(candidates, key=lambda q: abs(float(q.get("timestamp", 0.0)) - at), default=None)
        item = dict(connection)
        item["target_domain"] = str(nearest.get("domain")) if nearest else "ip-direct"
        merged.append(item)
    return merged


async def list_accesses(
    db: AsyncSession,
    *,
    run_id: str | None = None,
    agent_id: str | None = None,
    action: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[EgressAccess], int, dict[str, int]]:
    filters: list[Any] = []
    if run_id:
        filters.append(EgressAccess.run_id == run_id)
    if agent_id:
        filters.append(EgressAccess.agent_id == agent_id)
    if action:
        filters.append(EgressAccess.action == action)
    total = int((await db.execute(select(func.count()).select_from(EgressAccess).where(*filters))).scalar_one())
    rows = (
        await db.execute(
            select(EgressAccess)
            .where(*filters)
            .order_by(EgressAccess.occurred_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()
    summary_rows = (
        await db.execute(
            select(EgressAccess.action, func.count()).where(*filters).group_by(EgressAccess.action)
        )
    ).all()
    summary = {"allow": 0, "deny": 0, "ip_direct": 0}
    for key, count in summary_rows:
        summary[str(key)] = int(count)
    summary["ip_direct"] = int(
        (
            await db.execute(
                select(func.count()).select_from(EgressAccess).where(
                    *filters, EgressAccess.target_domain == "ip-direct"
                )
            )
        ).scalar_one()
    )
    return list(rows), total, summary
