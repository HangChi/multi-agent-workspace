"""从模板创建 / 一键克隆的集成测试（T025 / T026 / T027）。

这里验的是**不能靠单测糊过去**的部分：真实数据库的归属校验、真实工具目录的
启用态、真实外键下的「不复制实例级数据」。
"""

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.agent_template import AgentTemplate
from app.models.audit_log import AuditLog
from app.models.egress import EgressRule
from app.models.group import AgentGroup
from app.models.group_member import GroupMember
from app.models.run import Run
from app.models.user import User

pytestmark = pytest.mark.integration


async def _user(session: AsyncSession, email: str) -> User:
    user = User(email=email, password_hash="x" * 60, role="member", status="active")
    session.add(user)
    await session.commit()
    return user


def _payload(**overrides) -> dict:
    base = {"name": "新 Agent", "system_prompt": "p", "model": "m"}
    base.update(overrides)
    return base


async def _template_values(tpl) -> dict:
    """把模板的 10 个配置字段摊平成 AgentCreate —— 前端预填后原样提交的形态。"""
    return {
        "name": "我的 Agent",
        "description": tpl.description,
        "system_prompt": tpl.system_prompt,
        "model": tpl.model,
        "temperature": tpl.temperature,
        "max_steps": tpl.max_steps,
        "max_run_seconds": tpl.max_run_seconds,
        "tools": tpl.tools,
        "cost_limit_cents": tpl.cost_limit_cents,
        "egress_mode": tpl.egress_mode,
    }


async def _make_agent(session: AsyncSession, owner_id: str, **overrides) -> Agent:
    base = {
        "owner_id": owner_id,
        "name": "源 Agent",
        "description": "源描述",
        "system_prompt": "源提示词",
        "model": "源模型",
        "temperature": 0.33,
        "max_steps": 4,
        "max_run_seconds": 222,
        "tools": ["calculate"],
        "egress_mode": "default",
        "cost_limit_cents": 333,
    }
    base.update(overrides)
    agent = Agent(**base)
    session.add(agent)
    await session.commit()
    return agent


async def _make_template(
    session: AsyncSession, owner_id: str | None, **overrides
) -> AgentTemplate:
    base = {
        "owner_id": owner_id,
        "name": "模板",
        "description": "模板描述",
        "system_prompt": "模板提示词",
        "model": "模板模型",
        "temperature": 0.44,
        "max_steps": 6,
        "max_run_seconds": 111,
        "tools": ["calculate"],
        "cost_limit_cents": 444,
        "egress_mode": "default",
    }
    base.update(overrides)
    tpl = AgentTemplate(**base)
    session.add(tpl)
    await session.commit()
    return tpl


# ===== US1：从模板创建 =====


@pytest.mark.asyncio
async def test_create_from_template_matches_template_field_for_field(
    client, db_session, auth_headers
):
    headers, owner = await auth_headers()
    tpl = await _make_template(db_session, owner.id)

    resp = await client.post(
        f"/api/v1/agents/from-template/{tpl.id}",
        json=await _template_values(tpl),
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "我的 Agent"
    assert body["description"] == "模板描述"
    assert body["system_prompt"] == "模板提示词"
    assert body["model"] == "模板模型"
    assert body["temperature"] == 0.44
    assert body["max_steps"] == 6
    assert body["max_run_seconds"] == 111
    assert body["tools"] == ["calculate"]
    assert body["cost_limit_cents"] == 444
    assert body["status"] == "active"
    # 新 Agent 没有任何实例级数据（Run 的执行体是 assignee_id/assignee_type）
    assert body["source_template_id"] == tpl.id
    assert body["source_agent_id"] is None
    runs = (
        await db_session.execute(
            select(func.count())
            .select_from(Run)
            .where(Run.assignee_id == body["id"], Run.assignee_type == "agent")
        )
    ).scalar_one()
    assert runs == 0


@pytest.mark.asyncio
async def test_create_from_template_with_edited_fields_only_those_differ(
    client, db_session, auth_headers
):
    headers, owner = await auth_headers()
    tpl = await _make_template(db_session, owner.id)

    resp = await client.post(
        f"/api/v1/agents/from-template/{tpl.id}",
        json={
            **(await _template_values(tpl)),
            "name": "改过的",
            "temperature": 1.9,
            "tools": [],
        },
        headers=headers,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["temperature"] == 1.9
    assert body["tools"] == []
    # 其余仍取模板
    assert body["system_prompt"] == "模板提示词"
    assert body["model"] == "模板模型"
    assert body["cost_limit_cents"] == 444


@pytest.mark.asyncio
async def test_create_from_template_filters_offline_tools_with_warning(
    client, db_session, auth_headers
):
    headers, owner = await auth_headers()
    tpl = await _make_template(
        db_session, owner.id, tools=["calculate", "definitely_offline"]
    )

    resp = await client.post(
        f"/api/v1/agents/from-template/{tpl.id}",
        json=await _template_values(tpl),
        headers=headers,
    )
    # 创建 MUST 成功，工具被过滤，名字进 warning（FR-008 / SC-005）
    assert resp.status_code == 201
    body = resp.json()
    assert body["tools"] == ["calculate"]
    assert "definitely_offline" in (body["warning"] or "")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad",
    [
        {"temperature": 9.9},
        {"system_prompt": ""},
        {"cost_limit_cents": 0},
        {"max_steps": 0},
        {"max_run_seconds": 0},
        {"egress_mode": "nope"},
    ],
)
async def test_create_from_template_enforces_the_same_validation(
    client, db_session, auth_headers, bad
):
    """模板路径不得成为绕过校验的旁路（FR-009 / FR-022）。

    `tools` 不在这个列表里：FR-008 明确要求该字段「过滤 + 提示」而不是拒绝，
    见 test_create_from_template_filters_payload_tools_too。
    """
    headers, owner = await auth_headers()
    tpl = await _make_template(db_session, owner.id)
    resp = await client.post(
        f"/api/v1/agents/from-template/{tpl.id}",
        json=_payload(**bad),
        headers=headers,
    )
    assert resp.status_code in (400, 422)


@pytest.mark.asyncio
async def test_create_from_template_filters_payload_tools_too(
    client, db_session, auth_headers
):
    """用户提交里夹带失效工具同样过滤 + 提示（FR-008 覆盖「最终工具集」）。"""
    headers, owner = await auth_headers()
    tpl = await _make_template(db_session, owner.id)
    resp = await client.post(
        f"/api/v1/agents/from-template/{tpl.id}",
        json=_payload(tools=["calculate", "ghost_tool"]),
        headers=headers,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["tools"] == ["calculate"]
    assert "ghost_tool" in (body["warning"] or "")


@pytest.mark.asyncio
async def test_create_from_builtin_template_is_allowed_for_any_member(
    client, db_session, auth_headers
):
    _headers, owner = await auth_headers()
    builtin = await _make_template(db_session, None, name=f"内置-{owner.id[:8]}")
    headers, other = await auth_headers()

    resp = await client.post(
        f"/api/v1/agents/from-template/{builtin.id}",
        json=_payload(),
        headers=headers,
    )
    assert resp.status_code == 201
    assert resp.json()["owner_id"] == other.id


@pytest.mark.asyncio
async def test_create_from_foreign_template_404_identical_body(
    client, db_session, auth_headers
):
    _h, owner = await auth_headers()
    tpl = await _make_template(db_session, owner.id)
    headers, _stranger = await auth_headers()

    foreign = await client.post(
        f"/api/v1/agents/from-template/{tpl.id}", json=_payload(), headers=headers
    )
    assert foreign.status_code == 404
    assert foreign.json()["error"]["message"] == "模板不存在"


@pytest.mark.asyncio
async def test_viewer_cannot_create_from_template(client, db_session, auth_headers):
    _h, owner = await auth_headers()
    tpl = await _make_template(db_session, owner.id)
    headers, _viewer = await auth_headers(role="viewer")
    resp = await client.post(
        f"/api/v1/agents/from-template/{tpl.id}", json=_payload(), headers=headers
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_create_from_template_writes_audit(client, db_session, auth_headers):
    headers, owner = await auth_headers()
    tpl = await _make_template(db_session, owner.id)
    resp = await client.post(
        f"/api/v1/agents/from-template/{tpl.id}", json=_payload(), headers=headers
    )
    assert resp.status_code == 201
    actions = set(
        (
            await db_session.execute(
                select(AuditLog.action).where(AuditLog.user_id == owner.id)
            )
        )
        .scalars()
        .all()
    )
    assert "agent_create_from_template" in actions


# ===== US2：一键克隆 =====


@pytest.mark.asyncio
async def test_clone_copies_config_and_starts_empty(client, db_session, auth_headers):
    headers, owner = await auth_headers()
    source = await _make_agent(db_session, owner.id)

    resp = await client.post(f"/api/v1/agents/{source.id}/clone", headers=headers)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "源 Agent 副本"
    assert body["description"] == "源描述"
    assert body["system_prompt"] == "源提示词"
    assert body["model"] == "源模型"
    assert body["temperature"] == 0.33
    assert body["max_steps"] == 4
    assert body["max_run_seconds"] == 222
    assert body["tools"] == ["calculate"]
    assert body["cost_limit_cents"] == 333
    assert body["source_agent_id"] == source.id
    assert body["source_template_id"] is None
    assert body["status"] == "active"

    # 产物在数据库里也是干净的
    clone_id = body["id"]
    runs = (
        await db_session.execute(
            select(func.count())
            .select_from(Run)
            .where(Run.assignee_id == clone_id, Run.assignee_type == "agent")
        )
    ).scalar_one()
    assert runs == 0


@pytest.mark.asyncio
async def test_clone_does_not_copy_instance_level_data(
    client, db_session, auth_headers
):
    """SC-003：组引用、授权、出站规则、运行历史全部为 0，源对象一个不少。"""
    headers, owner = await auth_headers()
    source = await _make_agent(db_session, owner.id, egress_mode="restricted")
    group = AgentGroup(owner_id=owner.id, name="g", routing_strategy="direct")
    db_session.add(group)
    await db_session.flush()
    db_session.add(GroupMember(group_id=group.id, agent_id=source.id, position=0))
    db_session.add(
        EgressRule(
            scope="agent",
            agent_id=source.id,
            action="allow",
            target_type="domain",
            target="example.com",
            enabled=True,
            created_by=owner.id,
        )
    )
    await db_session.commit()

    resp = await client.post(f"/api/v1/agents/{source.id}/clone", headers=headers)
    assert resp.status_code == 201, resp.text
    clone_id = resp.json()["id"]

    members = (
        await db_session.execute(
            select(func.count())
            .select_from(GroupMember)
            .where(GroupMember.agent_id == clone_id)
        )
    ).scalar_one()
    rules = (
        await db_session.execute(
            select(func.count())
            .select_from(EgressRule)
            .where(EgressRule.agent_id == clone_id)
        )
    ).scalar_one()
    runs = (
        await db_session.execute(
            select(func.count())
            .select_from(Run)
            .where(Run.assignee_id == clone_id, Run.assignee_type == "agent")
        )
    ).scalar_one()
    assert (members, rules, runs) == (0, 0, 0)

    # 外部工具授权没有在这里搭：一个 grant 需要 server → version → listing →
    # installation 四层链。但克隆体的 id 是全新的，任何子表都不可能引用它；
    # 且 `clone_agent` 只写 SNAPSHOT_FIELDS（单测已逐字段锁定），
    # 授权/规则/成员一概不碰。

    # 源的组引用数不变（SC-009）
    source_members = (
        await db_session.execute(
            select(func.count())
            .select_from(GroupMember)
            .where(GroupMember.agent_id == source.id)
        )
    ).scalar_one()
    assert source_members == 1


@pytest.mark.asyncio
async def test_clone_restricted_without_rules_warns(client, db_session, auth_headers):
    """FR-012：restricted 且无 allow 规则 → 成功 + 与 POST /agents 同一句警告。"""
    headers, owner = await auth_headers()
    source = await _make_agent(db_session, owner.id, egress_mode="restricted")

    resp = await client.post(f"/api/v1/agents/{source.id}/clone", headers=headers)
    assert resp.status_code == 201
    assert resp.json()["egress_mode"] == "restricted"
    assert (
        resp.json()["warning"] == "限制模式下尚无启用的允许规则，运行时全部出站被拒。"
    )


@pytest.mark.asyncio
async def test_three_consecutive_clones_get_distinct_names(
    client, db_session, auth_headers
):
    headers, owner = await auth_headers()
    source = await _make_agent(db_session, owner.id)

    names = []
    for _ in range(3):
        resp = await client.post(f"/api/v1/agents/{source.id}/clone", headers=headers)
        assert resp.status_code == 201
        names.append(resp.json()["name"])
    assert names == ["源 Agent 副本", "源 Agent 副本2", "源 Agent 副本3"]


@pytest.mark.asyncio
async def test_clone_archived_source_yields_active_agent(
    client, db_session, auth_headers
):
    headers, owner = await auth_headers()
    source = await _make_agent(db_session, owner.id, status="archived")

    resp = await client.post(f"/api/v1/agents/{source.id}/clone", headers=headers)
    assert resp.status_code == 201
    assert resp.json()["status"] == "active"


@pytest.mark.asyncio
async def test_clone_foreign_agent_404(client, db_session, auth_headers):
    _h, owner = await auth_headers()
    source = await _make_agent(db_session, owner.id)
    headers, _stranger = await auth_headers()

    resp = await client.post(f"/api/v1/agents/{source.id}/clone", headers=headers)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_source_agent_keeps_clone(client, db_session, auth_headers):
    headers, owner = await auth_headers()
    source = await _make_agent(db_session, owner.id)
    clone_resp = await client.post(f"/api/v1/agents/{source.id}/clone", headers=headers)
    assert clone_resp.status_code == 201
    clone_id = clone_resp.json()["id"]

    deleted = await client.delete(f"/api/v1/agents/{source.id}", headers=headers)
    assert deleted.status_code == 204

    clone = (
        await db_session.execute(select(Agent).where(Agent.id == clone_id))
    ).scalar_one()
    assert clone.source_agent_id is None
    assert clone.name == "源 Agent 副本"


@pytest.mark.asyncio
async def test_clone_writes_audit(client, db_session, auth_headers):
    headers, owner = await auth_headers()
    source = await _make_agent(db_session, owner.id)
    resp = await client.post(f"/api/v1/agents/{source.id}/clone", headers=headers)
    assert resp.status_code == 201
    actions = set(
        (
            await db_session.execute(
                select(AuditLog.action).where(AuditLog.user_id == owner.id)
            )
        )
        .scalars()
        .all()
    )
    assert "agent_clone" in actions
