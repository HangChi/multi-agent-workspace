"""Agent 组端点（T036）：CRUD + 成员增删 + 排序。

对齐 contracts/api.md Agent 组部分：
- GET /groups → 200 GroupList（含成员展开）
- POST /groups → 201
- PATCH /groups/{id} → 200
- POST /groups/{id}/members → 201（重复/位置冲突 409）
- PATCH /groups/{id}/members → 200（排序保存）
- DELETE /groups/{id}/members/{agent_id} → 204
- DELETE /groups/{id} → 204（级联清空成员）
"""

from fastapi import APIRouter, Query

from app.api.deps import DB, CurrentUser, MemberUser, ResourceId
from app.core.audit import write_audit
from app.models.group import AgentGroup
from app.models.group_member import GroupMember
from app.schemas.group import (
    GroupCreate,
    GroupList,
    GroupMemberAdd,
    GroupMemberOut,
    GroupMemberReorder,
    GroupOut,
    GroupUpdate,
    GroupWithMembers,
)
from app.services import group_service

router = APIRouter(prefix="/groups", tags=["groups"])


def _to_with_members(group: AgentGroup, members: list[GroupMember]) -> GroupWithMembers:
    """组 → GroupWithMembers（成员展开含 Agent 名快照）。"""
    out = GroupOut.model_validate(group)
    member_outs = []
    for m in sorted(members, key=lambda x: x.position):
        member_outs.append(
            GroupMemberOut(
                agent_id=m.agent_id,
                name=m.agent.name if m.agent else m.agent_id,
                position=m.position,
            )
        )
    return GroupWithMembers(**out.model_dump(), members=member_outs)


@router.get("", response_model=GroupList)
async def list_groups(
    user: CurrentUser,
    _m: MemberUser,
    db: DB,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    all: bool = Query(
        default=False,
        description="返回全部命中项（忽略分页）。看板等需要完整目标集合的场景用。",
    ),
):
    """组列表（含成员展开）。

    `total` 为过滤后的全量条数，前端据此算总页数。
    """
    groups, total = await group_service.list_groups(
        db, user_id=user.id, page=page, page_size=page_size, fetch_all=all
    )
    items = [_to_with_members(g, list(g.members)) for g in groups]
    return {"items": items, "total": total}


@router.post("", response_model=GroupWithMembers, status_code=201)
async def create_group(payload: GroupCreate, _m: MemberUser, user: CurrentUser, db: DB):
    """创建组（routing_strategy 默认 fan_out）。"""
    group = await group_service.create_group(db, user_id=user.id, payload=payload)
    await db.refresh(group)
    await write_audit(
        db,
        user_id=user.id,
        action="group_create",
        target_type="group",
        target_id=group.id,
        detail={"name": group.name, "routing_strategy": group.routing_strategy},
        strict=True,
    )
    await db.commit()
    return _to_with_members(group, [])


@router.patch("/{group_id}", response_model=GroupOut)
async def update_group(
    group_id: ResourceId,
    payload: GroupUpdate,
    user: CurrentUser,
    db: DB,
):
    """编辑组（归属校验）。"""
    group = await group_service.update_group(
        db, group_id=group_id, user_id=user.id, payload=payload
    )
    await db.commit()
    await db.refresh(group)
    return GroupOut.model_validate(group)


@router.get("/{group_id}", response_model=GroupWithMembers)
async def get_group(
    group_id: ResourceId,
    user: CurrentUser,
    _m: MemberUser,
    db: DB,
):
    """组详情（含成员展开）。"""
    group = await group_service.get_group(db, group_id=group_id, user_id=user.id)
    return _to_with_members(group, list(group.members))


@router.post("/{group_id}/members", response_model=GroupMemberOut, status_code=201)
async def add_member(
    group_id: ResourceId,
    payload: GroupMemberAdd,
    _m: MemberUser,
    user: CurrentUser,
    db: DB,
):
    """添加成员（重复/位置冲突 409，归档 Agent 400）。"""
    from sqlalchemy import select

    from app.models.agent import Agent

    member = await group_service.add_member(
        db,
        group_id=group_id,
        user_id=user.id,
        agent_id=payload.agent_id,
        position=payload.position,
    )
    # 预取 Agent 名（避免 async 懒加载 MissingGreenlet）
    agent = (
        (await db.execute(select(Agent).where(Agent.id == member.agent_id)))
        .scalars()
        .first()
    )
    await db.refresh(member)
    await write_audit(
        db,
        user_id=user.id,
        action="group_member_change",
        target_type="group",
        target_id=group_id,
        detail={"op": "add", "agent_id": member.agent_id, "position": member.position},
        strict=True,
    )
    await db.commit()
    return GroupMemberOut(
        agent_id=member.agent_id,
        name=agent.name if agent else member.agent_id,
        position=member.position,
    )


@router.patch("/{group_id}/members", response_model=GroupWithMembers)
async def reorder_members(
    group_id: ResourceId,
    payload: GroupMemberReorder,
    _m: MemberUser,
    user: CurrentUser,
    db: DB,
):
    """排序保存（positions 整体替换）。"""
    positions = [(p.agent_id, p.position) for p in payload.positions]
    await group_service.reorder_members(
        db, group_id=group_id, user_id=user.id, positions=positions
    )
    group = await group_service.get_group(db, group_id=group_id, user_id=user.id)
    await write_audit(
        db,
        user_id=user.id,
        action="group_member_change",
        target_type="group",
        target_id=group_id,
        detail={"op": "reorder", "positions": positions},
        strict=True,
    )
    await db.commit()
    return _to_with_members(group, list(group.members))


@router.delete("/{group_id}/members/{agent_id}", status_code=204)
async def remove_member(
    group_id: ResourceId,
    agent_id: ResourceId,
    _m: MemberUser,
    user: CurrentUser,
    db: DB,
):
    """移除成员（归属校验）。"""
    await group_service.remove_member(
        db, group_id=group_id, user_id=user.id, agent_id=agent_id
    )
    await write_audit(
        db,
        user_id=user.id,
        action="group_member_change",
        target_type="group",
        target_id=group_id,
        detail={"op": "remove", "agent_id": agent_id},
        strict=True,
    )
    await db.commit()


@router.delete("/{group_id}", status_code=204)
async def delete_group(
    group_id: ResourceId,
    _m: MemberUser,
    user: CurrentUser,
    db: DB,
):
    """删除组（级联清空成员）。"""
    await group_service.delete_group(db, group_id=group_id, user_id=user.id)
    await write_audit(
        db,
        user_id=user.id,
        action="group_delete",
        target_type="group",
        target_id=group_id,
        strict=True,
    )
    await db.commit()
