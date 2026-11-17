"""router 策略：用 LLM 从组成员中选一个最合适的 Agent 执行。

与另外两种组策略的区别：
- direct：直接交给单个 Agent（无组）
- fan_out：全员并发，结果汇总
- pipeline：按 position 顺序串联，前者输出作为后者输入
- **router**：只选一个成员，把整件事交给它（"按内容分发"）

选择过程本身也是一次 LLM 调用，成本计入该 Run（不能让路由开销消失在账外）。
失败降级：解析不出编号时按顺序取第一个，避免路由失败导致整个任务失败。
"""

from dataclasses import dataclass

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.executors.llm import LiteLLMClient, LLMClientError
from app.models.agent import Agent
from app.models.group import AgentGroup
from app.models.group_member import GroupMember

logger = structlog.get_logger(__name__)

_ROUTER_SYSTEM_PROMPT = (
    "你是任务路由器。给定一个需求和若干候选专家，选出最适合完成该需求的一个。\n"
    "只输出候选编号（阿拉伯数字），不要输出任何其他文字、标点或解释。"
)

_MAX_PARSE_CHARS = 32


@dataclass
class RoutingDecision:
    """路由结果：选中的 Agent + 依据 + 决策本身消耗的成本（分）。"""

    agent: Agent
    reason: str
    cost_cents: int = 0


def _parse_index(text: str, upper_bound: int) -> int | None:
    """从 LLM 输出里解析 1-based 编号；解析不出或越界返回 None。"""
    cleaned = (text or "").strip()[:_MAX_PARSE_CHARS]
    digits = ""
    for char in cleaned:
        if char.isdigit():
            digits += char
        elif digits:
            break
        elif char == "-":
            # 负数不是合法编号（"-1" 不能被当成 1）
            return None
    if not digits:
        return None
    index = int(digits)
    if 1 <= index <= upper_bound:
        return index
    return None


async def load_active_members(db: AsyncSession, group_id: str) -> list[Agent]:
    """按 position 升序返回组内可用成员（已归档的排除）。"""
    stmt = (
        select(AgentGroup)
        .options(selectinload(AgentGroup.members).selectinload(GroupMember.agent))
        .where(AgentGroup.id == group_id)
    )
    group = (await db.execute(stmt)).scalars().first()
    if group is None:
        return []
    members = sorted(group.members, key=lambda m: m.position)
    return [
        m.agent for m in members if m.agent is not None and m.agent.status == "active"
    ]


def _describe(members: list[Agent]) -> str:
    lines = []
    for index, agent in enumerate(members, start=1):
        desc = (agent.description or "").strip() or "（无描述）"
        lines.append(f"{index}. {agent.name}：{desc}")
    return "\n".join(lines)


async def route(
    db: AsyncSession,
    *,
    group_id: str,
    request_text: str,
    llm: LiteLLMClient | None = None,
) -> RoutingDecision | None:
    """选择执行成员；组内无可用成员返回 None。"""
    members = await load_active_members(db, group_id)
    if not members:
        return None
    if len(members) == 1:
        return RoutingDecision(agent=members[0], reason="组内仅一个可用成员，无需路由")

    client = llm or LiteLLMClient()
    messages = [
        {"role": "system", "content": _ROUTER_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"需求：\n{request_text}\n\n候选专家：\n{_describe(members)}\n\n请只回复编号。"
            ),
        },
    ]

    try:
        # temperature=0：同一需求应稳定命中同一成员，避免抖动
        response = await client.chat(messages, temperature=0.0, max_tokens=8)
    except LLMClientError as exc:
        logger.warning("router_llm_failed", group_id=group_id[:8], error=str(exc))
        return RoutingDecision(
            agent=members[0], reason=f"路由模型不可用，降级为第一个成员（{exc}）"
        )

    index = _parse_index(response.content, len(members))
    if index is None:
        logger.warning(
            "router_parse_failed",
            group_id=group_id[:8],
            raw=(response.content or "")[:60],
        )
        return RoutingDecision(
            agent=members[0],
            reason=f"路由输出无法解析（{response.content!r}），降级为第一个成员",
            cost_cents=response.cost_cents,
        )

    chosen = members[index - 1]
    return RoutingDecision(
        agent=chosen,
        reason=f"路由模型选择第 {index} 个成员：{chosen.name}",
        cost_cents=response.cost_cents,
    )
