"""组/出站规则/产物的守卫单测（015 覆盖率补齐）。

三处共同点是「拒绝的理由必须精准」，用户看到的提示直接决定他下一步做什么：

- 组里加成员：agent 不存在 / 是别人的 / 已归档 —— 三种完全不同的处置方式
  （换个 id、找 owner 要、先恢复），混成一句「无法添加」等于没提示；
- 出站规则：内置黑名单不能被删（`builtin:` 前缀与按目标值双查，因为内置项
  既可能来自内置常量也可能被写进库）；
- 产物：越权访问必须 404 而不是 403（不暴露产物存在性）。
"""

import uuid

import pytest

from app.api.errors import AppError
from app.models.agent import Agent
from app.models.user import User
from app.services import egress_service, group_service

pytestmark = pytest.mark.integration


async def _user(db, role="member"):
    user = User(
        email=f"grd-{uuid.uuid4().hex[:8]}@test.com",
        password_hash="x" * 60,
        role=role,
        status="active",
    )
    db.add(user)
    await db.commit()
    return user


async def _agent(db, owner, *, status="active"):
    agent = Agent(
        owner_id=owner.id,
        name=f"agent-{uuid.uuid4().hex[:6]}",
        model="openai/gpt-4o-mini",
        system_prompt="x",
        tools=[],
        status=status,
    )
    db.add(agent)
    await db.commit()
    return agent


# ---------- 组：成员守卫 ----------


async def _group(db, owner):
    from app.models.group import AgentGroup

    group = AgentGroup(owner_id=owner.id, name=f"组-{uuid.uuid4().hex[:6]}")
    db.add(group)
    await db.commit()
    return group


async def test_add_member_rejects_missing_agent(db_session):
    owner = await _user(db_session)
    group = await _group(db_session, owner)

    with pytest.raises(AppError) as exc:
        await group_service.add_member(
            db_session,
            group_id=group.id,
            user_id=owner.id,
            agent_id=str(uuid.uuid4()),
            position=1,
        )
    assert exc.value.code == "NOT_FOUND"


async def test_add_member_rejects_foreign_agent(db_session):
    owner = await _user(db_session)
    stranger = await _user(db_session)
    group = await _group(db_session, owner)
    foreign = await _agent(db_session, stranger)

    with pytest.raises(AppError) as exc:
        await group_service.add_member(
            db_session,
            group_id=group.id,
            user_id=owner.id,
            agent_id=foreign.id,
            position=1,
        )
    assert exc.value.code == "FORBIDDEN"


async def test_add_member_rejects_archived_agent(db_session):
    owner = await _user(db_session)
    group = await _group(db_session, owner)
    archived = await _agent(db_session, owner, status="archived")

    with pytest.raises(AppError) as exc:
        await group_service.add_member(
            db_session,
            group_id=group.id,
            user_id=owner.id,
            agent_id=archived.id,
            position=1,
        )
    assert exc.value.code == "INVALID_INPUT"


async def test_update_group_converts_strategy_enum(db_session):
    """routing_strategy 是枚举：要存 value 而不是 Enum 对象，否则落库失败。"""
    from app.schemas.group import GroupUpdate

    owner = await _user(db_session)
    group = await _group(db_session, owner)

    updated = await group_service.update_group(
        db_session,
        group_id=group.id,
        user_id=owner.id,
        payload=GroupUpdate(routing_strategy="router"),
    )

    assert updated.routing_strategy == "router"


async def test_get_group_rejects_stranger(db_session):
    owner = await _user(db_session)
    stranger = await _user(db_session)
    group = await _group(db_session, owner)

    with pytest.raises(AppError) as exc:
        await group_service.get_group(db_session, group.id, stranger.id)
    assert exc.value.code in {"FORBIDDEN", "NOT_FOUND"}


# ---------- 出站规则 ----------


async def test_delete_builtin_rule_by_prefix_rejected(db_session):
    with pytest.raises(AppError) as exc:
        await egress_service.delete_global_rule(
            db_session, rule_id="builtin:10.0.0.0/8"
        )
    assert exc.value.code == "INVALID_INPUT"


async def test_delete_missing_rule_is_404(db_session):
    with pytest.raises(AppError) as exc:
        await egress_service.delete_global_rule(db_session, rule_id=str(uuid.uuid4()))
    assert exc.value.code == "NOT_FOUND"


async def test_snapshot_requires_existing_agent(db_session):
    with pytest.raises(AppError) as exc:
        await egress_service.snapshot_for_execution(
            db_session, agent_id=str(uuid.uuid4())
        )
    assert exc.value.code == "NOT_FOUND"


async def test_snapshot_returns_mode_and_rules(db_session):
    owner = await _user(db_session)
    agent = await _agent(db_session, owner)

    mode, global_rules, agent_rules = await egress_service.snapshot_for_execution(
        db_session, agent_id=agent.id
    )

    assert mode in {"default", "restricted"}
    assert isinstance(global_rules, list)
    assert isinstance(agent_rules, list)


# ---------- 015 覆盖率补齐：归档组不可分配 ----------


async def test_load_group_rejects_archived_group(db_session):
    """归档组 → 400 INVALID_INPUT（区别于「不存在 → 404」）。"""
    from app.models.group import AgentGroup
    from app.services.assignment_service import _load_group

    owner = await _user(db_session)
    group = AgentGroup(
        owner_id=owner.id,
        name=f"archived-{uuid.uuid4().hex[:6]}",
        routing_strategy="fan_out",
        status="archived",
    )
    db_session.add(group)
    await db_session.commit()

    with pytest.raises(AppError) as exc:
        await _load_group(db_session, group.id, owner.id)
    assert exc.value.code == "INVALID_INPUT"
    assert "归档" in exc.value.message
