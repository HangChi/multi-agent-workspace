"""Agent 组服务（T034）：CRUD + 成员增删（position 唯一处理）+ 级联清理 + 归属校验。

对齐 contracts/api.md Agent 组部分：
- 添加成员：重复 agent → 409，position 冲突 → 409，归档 Agent → 400
- 排序：positions 数组整体替换
- 删除组：级联清空成员
- 归属校验：组与成员的双向校验（自己的 Agent 不能加入他人组）
"""

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.errors import AppError
from app.models.group import AgentGroup
from app.models.group_member import GroupMember
from app.schemas.group import GroupCreate, GroupUpdate
from app.services import agent_service


def _check_owner(group: AgentGroup, user_id: str) -> None:
    """非 owner 一律 404（016）：与「组不存在」同响应。"""
    if group.owner_id != user_id:
        raise AppError("NOT_FOUND", "组不存在")


def _with_members() -> Any:
    """预加载 members.agent，避免 async 懒加载 MissingGreenlet。"""
    return selectinload(AgentGroup.members).selectinload(GroupMember.agent)


async def create_group(
    db: AsyncSession, *, user_id: str, payload: GroupCreate
) -> AgentGroup:
    """创建组（routing_strategy 默认 fan_out）。"""
    group = AgentGroup(
        owner_id=user_id,
        name=payload.name,
        description=payload.description,
        routing_strategy=payload.routing_strategy.value,
    )
    db.add(group)
    await db.flush()
    return group


async def get_group(db: AsyncSession, group_id: str, user_id: str) -> AgentGroup:
    """取单个组（归属校验，预加载成员）。"""
    stmt = select(AgentGroup).options(_with_members()).where(AgentGroup.id == group_id)
    group = (await db.execute(stmt)).scalars().first()
    if group is None:
        raise AppError("NOT_FOUND", "组不存在")
    _check_owner(group, user_id)
    return group


async def list_groups(
    db: AsyncSession,
    *,
    user_id: str,
    include_archived: bool = False,
    page: int = 1,
    page_size: int = 20,
    fetch_all: bool = False,
) -> tuple[list[AgentGroup], int]:
    """列表：仅自己的（默认 active），预加载成员，支持分页。

    返回 `(当前页, 总数)`；total 是过滤后全量条数（不是当页长度）。

    `fetch_all=True` 供看板当拖拽目标用：必须拿全集，否则超过一页的组
    会从拖拽区静默消失。
    """
    conditions = [AgentGroup.owner_id == user_id]
    if not include_archived:
        conditions.append(AgentGroup.status == "active")

    total = (
        await db.execute(
            select(func.count()).select_from(AgentGroup).where(*conditions)
        )
    ).scalar_one()

    stmt = (
        select(AgentGroup)
        .options(_with_members())
        .where(*conditions)
        # id 作 tie-breaker：同批创建的组 created_at 相同，只按时间排序不稳定
        .order_by(AgentGroup.created_at.desc(), AgentGroup.id.desc())
    )
    if not fetch_all:
        stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    rows = list((await db.execute(stmt)).scalars().all())
    return rows, total


async def update_group(
    db: AsyncSession, *, group_id: str, user_id: str, payload: GroupUpdate
) -> AgentGroup:
    """编辑组（归属校验）。"""
    group = await get_group(db, group_id, user_id)
    data = payload.model_dump(exclude_unset=True)
    if "routing_strategy" in data and data["routing_strategy"] is not None:
        data["routing_strategy"] = data["routing_strategy"].value
    for field, value in data.items():
        setattr(group, field, value)
    await db.flush()
    return group


async def add_member(
    db: AsyncSession, *, group_id: str, user_id: str, agent_id: str, position: int
) -> GroupMember:
    """添加成员：agent 必须存在、归属当前用户、active 状态；position 不冲突。"""
    await get_group(db, group_id, user_id)

    # R1：归属谓词走 `agent_service.get_agent`（单一判定点）。
    # 016：他人的 Agent 与「不存在」同响应，否则可以拿组的成员接口探测
    # 某个 Agent id 是否存在（403 = 存在但不归你）。
    agent = await agent_service.get_agent(db, agent_id, user_id)
    if agent.status != "active":
        raise AppError("INVALID_INPUT", "归档 Agent 不可加入组")

    # 重复成员 / position 冲突
    existing = (
        (
            await db.execute(
                select(GroupMember).where(
                    GroupMember.group_id == group_id,
                    GroupMember.agent_id == agent_id,
                )
            )
        )
        .scalars()
        .first()
    )
    if existing is not None:
        raise AppError("CONFLICT", "该 Agent 已在组中")

    pos_taken = (
        (
            await db.execute(
                select(GroupMember).where(
                    GroupMember.group_id == group_id,
                    GroupMember.position == position,
                )
            )
        )
        .scalars()
        .first()
    )
    if pos_taken is not None:
        raise AppError("CONFLICT", "该位置已被占用")

    member = GroupMember(group_id=group_id, agent_id=agent_id, position=position)
    db.add(member)
    await db.flush()
    return member


async def remove_member(
    db: AsyncSession, *, group_id: str, user_id: str, agent_id: str
) -> None:
    """移除成员（归属校验）。"""
    group = await get_group(db, group_id, user_id)
    member = (
        (
            await db.execute(
                select(GroupMember).where(
                    GroupMember.group_id == group.id,
                    GroupMember.agent_id == agent_id,
                )
            )
        )
        .scalars()
        .first()
    )
    if member is None:
        raise AppError("NOT_FOUND", "组内不存在该成员")
    await db.delete(member)
    await db.flush()


async def reorder_members(
    db: AsyncSession,
    *,
    group_id: str,
    user_id: str,
    positions: list[tuple[str, int]],
) -> list[GroupMember]:
    """排序保存：positions 整体替换（agent_id → position）。

    先清空（临时负 position）再赋值，规避 uq_group_position 唯一约束在
    成员互换场景下的瞬时冲突。
    """
    group = await get_group(db, group_id, user_id)
    members = list(
        (await db.execute(select(GroupMember).where(GroupMember.group_id == group.id)))
        .scalars()
        .all()
    )
    by_id = {m.agent_id: m for m in members}

    pos_map: dict[int, str] = {}
    for agent_id, position in positions:
        if agent_id not in by_id:
            raise AppError("NOT_FOUND", f"组内不存在 Agent {agent_id}")
        if position in pos_map:
            raise AppError("CONFLICT", "排序中存在重复 position")
        pos_map[position] = agent_id

    # positions 是「整体替换」语义：既要覆盖组内全部成员，也不能对同一成员重复
    # 赋值（后者会让某个 position 被静默丢弃）。校验放在任何写入之前，且必须是
    # 4xx：漏传成员的客户端快照过期是常见情况，让 next() 抛 StopIteration 在协程
    # 内会升级为 RuntimeError → 500。
    target = {agent_id: position for position, agent_id in pos_map.items()}
    if len(target) != len(pos_map):
        raise AppError("INVALID_INPUT", "排序中存在重复成员")
    missing = sorted(set(by_id) - set(target))
    if missing or set(target) - set(by_id):
        raise AppError(
            "INVALID_INPUT",
            "排序必须覆盖组内全部成员",
            detail={"missing_agent_ids": missing[:20]},
        )

    # 第一步：全部置为临时负值，释放唯一约束
    for i, member in enumerate(members):
        member.position = -(i + 1)
    await db.flush()

    # 第二步：按目标位置赋值
    for member in members:
        member.position = target[member.agent_id]
    await db.flush()
    return members


async def delete_group(db: AsyncSession, *, group_id: str, user_id: str) -> None:
    """删除组：cascade 清空成员（relationship cascade all, delete-orphan）。"""
    group = await get_group(db, group_id, user_id)
    await db.delete(group)
    await db.flush()
