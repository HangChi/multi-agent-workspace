"""`/api/v1/agent-templates` 端点集成测试（T017）。

覆盖 contracts/api.md §1：列表 scope/q/分页、详情、PATCH、DELETE、
save-as-template 的 409，以及三类越权（viewer / 他人 / 内置）。
"""

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.agent_template import AgentTemplate
from app.models.audit_log import AuditLog
from app.models.user import User

pytestmark = pytest.mark.integration


async def _user(session: AsyncSession, email: str, role: str = "member") -> User:
    user = User(email=email, password_hash="x" * 60, role=role, status="active")
    session.add(user)
    # 必须 commit：API 走自己的 session，看不到本 session 未提交的数据
    await session.commit()
    return user


async def _agent(session: AsyncSession, owner_id: str, name: str = "a") -> Agent:
    agent = Agent(
        owner_id=owner_id,
        name=name,
        system_prompt="p",
        model="m",
        temperature=0.7,
        max_steps=10,
        max_run_seconds=300,
        tools=[],
        egress_mode="default",
        cost_limit_cents=500,
    )
    session.add(agent)
    await session.commit()
    return agent


async def _builtin(session: AsyncSession, name: str | None = None) -> AgentTemplate:
    """内置模板名带随机后缀：部分唯一索引是全局的，跨测试重名会撞车。"""
    name = name or f"内置-{uuid.uuid4().hex[:8]}"
    tpl = AgentTemplate(owner_id=None, name=name, system_prompt="p", model="m")
    session.add(tpl)
    await session.commit()
    return tpl


def _payload(**overrides) -> dict:
    base = {"name": "tpl", "system_prompt": "p", "model": "m"}
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_list_returns_builtins_and_own_templates(
    client, db_session, auth_headers
):
    headers, owner = await auth_headers()
    await _builtin(db_session)
    for i in range(2):
        db_session.add(
            AgentTemplate(
                owner_id=owner.id, name=f"mine-{i}", system_prompt="p", model="m"
            )
        )
    await db_session.commit()

    resp = await client.get("/api/v1/agent-templates", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    names = {t["name"] for t in body["items"]}
    assert {"mine-0", "mine-1"} <= names
    # ⚠️ total 不能写绝对数：别的测试文件 commit 过的内置模板不会回滚，
    # 全量跑时它们仍在库里。只断言「至少我们造的这些」+「没有别人的私有模板」。
    assert body["total"] >= 3
    assert all(t["is_builtin"] or t["owner_id"] == owner.id for t in body["items"])
    # 内置模板带只读标记，且 owner 为空
    builtin = next(t for t in body["items"] if t["is_builtin"])
    assert builtin["is_builtin"] is True
    assert builtin["owner_id"] is None


@pytest.mark.asyncio
async def test_list_scope_and_search_and_pagination(client, db_session, auth_headers):
    headers, owner = await auth_headers()
    await _builtin(db_session)
    for i in range(3):
        db_session.add(
            AgentTemplate(
                owner_id=owner.id, name=f"alpha-{i}", system_prompt="p", model="m"
            )
        )
    db_session.add(
        AgentTemplate(owner_id=owner.id, name="beta", system_prompt="p", model="m")
    )
    await db_session.commit()

    mine = await client.get(
        "/api/v1/agent-templates", params={"scope": "mine"}, headers=headers
    )
    assert mine.status_code == 200
    assert mine.json()["total"] == 4

    searched = await client.get(
        "/api/v1/agent-templates", params={"q": "alpha"}, headers=headers
    )
    assert searched.json()["total"] == 3

    page = await client.get(
        "/api/v1/agent-templates",
        params={"scope": "mine", "page": 1, "page_size": 2},
        headers=headers,
    )
    assert len(page.json()["items"]) == 2
    assert page.json()["total"] == 4


@pytest.mark.asyncio
async def test_viewer_is_forbidden(client, auth_headers):
    headers, _viewer = await auth_headers(role="viewer")
    resp = await client.get("/api/v1/agent-templates", headers=headers)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_patch_and_delete_own_template(client, db_session, auth_headers):
    headers, owner = await auth_headers()
    tpl = AgentTemplate(owner_id=owner.id, name="t", system_prompt="p", model="m")
    db_session.add(tpl)
    await db_session.commit()

    resp = await client.patch(
        f"/api/v1/agent-templates/{tpl.id}",
        json={"description": "new desc", "temperature": 1.5},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["description"] == "new desc"
    assert resp.json()["temperature"] == 1.5

    deleted = await client.delete(f"/api/v1/agent-templates/{tpl.id}", headers=headers)
    assert deleted.status_code == 204
    left = (
        await db_session.execute(
            select(AgentTemplate).where(AgentTemplate.id == tpl.id)
        )
    ).scalar_one_or_none()
    assert left is None


@pytest.mark.asyncio
async def test_patch_duplicate_name_returns_409(client, db_session, auth_headers):
    headers, owner = await auth_headers()
    db_session.add(
        AgentTemplate(owner_id=owner.id, name="one", system_prompt="p", model="m")
    )
    two = AgentTemplate(owner_id=owner.id, name="two", system_prompt="p", model="m")
    db_session.add(two)
    await db_session.commit()

    resp = await client.patch(
        f"/api/v1/agent-templates/{two.id}", json={"name": "one"}, headers=headers
    )
    assert resp.status_code == 409
    assert "同名" in resp.json()["error"]["message"]


@pytest.mark.asyncio
async def test_patch_invalid_field_returns_422(client, db_session, auth_headers):
    headers, owner = await auth_headers()
    tpl = AgentTemplate(owner_id=owner.id, name="t", system_prompt="p", model="m")
    db_session.add(tpl)
    await db_session.commit()

    resp = await client.patch(
        f"/api/v1/agent-templates/{tpl.id}",
        json={"temperature": 9.9},
        headers=headers,
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_foreign_template_returns_404_with_identical_body(
    client, db_session, auth_headers
):
    _headers, owner = await auth_headers()
    stranger_headers, _stranger = await auth_headers()
    tpl = AgentTemplate(owner_id=owner.id, name="secret", system_prompt="p", model="m")
    db_session.add(tpl)
    await db_session.commit()

    foreign = await client.get(
        f"/api/v1/agent-templates/{tpl.id}", headers=stranger_headers
    )
    missing = await client.get(
        "/api/v1/agent-templates/00000000-0000-0000-0000-000000000000",
        headers=stranger_headers,
    )
    assert foreign.status_code == 404
    assert missing.status_code == 404
    # 措辞必须逐字一致，否则等于告诉调用者「这个 id 存在」
    assert foreign.json() == missing.json()


@pytest.mark.asyncio
async def test_builtin_template_is_read_only(client, db_session, auth_headers):
    headers, _owner = await auth_headers()
    builtin = await _builtin(db_session)
    builtin_name = builtin.name

    patched = await client.patch(
        f"/api/v1/agent-templates/{builtin.id}",
        json={"name": "hijacked"},
        headers=headers,
    )
    assert patched.status_code == 403
    assert "内置" in patched.json()["error"]["message"]

    deleted = await client.delete(
        f"/api/v1/agent-templates/{builtin.id}", headers=headers
    )
    assert deleted.status_code == 403

    # 内置行本身没被改
    await db_session.refresh(builtin)
    assert builtin.name == builtin_name


@pytest.mark.asyncio
async def test_save_as_template_snapshots_agent(client, db_session, auth_headers):
    headers, owner = await auth_headers()
    agent = await _agent(db_session, owner.id)
    agent.system_prompt = "the prompt"
    agent.tools = ["calculate"]
    agent.temperature = 0.25
    await db_session.commit()

    resp = await client.post(
        f"/api/v1/agents/{agent.id}/save-as-template",
        json={"name": "沉淀", "description": "来自跑顺的 Agent"},
        headers=headers,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "沉淀"
    assert body["description"] == "来自跑顺的 Agent"
    assert body["system_prompt"] == "the prompt"
    assert body["tools"] == ["calculate"]
    assert body["temperature"] == 0.25
    assert body["is_builtin"] is False


@pytest.mark.asyncio
async def test_save_as_template_duplicate_name_409(client, db_session, auth_headers):
    headers, owner = await auth_headers()
    agent = await _agent(db_session, owner.id)
    db_session.add(
        AgentTemplate(owner_id=owner.id, name="已存在", system_prompt="p", model="m")
    )
    await db_session.commit()

    resp = await client.post(
        f"/api/v1/agents/{agent.id}/save-as-template",
        json={"name": "已存在"},
        headers=headers,
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_save_as_template_foreign_agent_404(client, db_session, auth_headers):
    _headers, owner = await auth_headers()
    stranger_headers, _stranger = await auth_headers()
    agent = await _agent(db_session, owner.id)
    await db_session.commit()

    resp = await client.post(
        f"/api/v1/agents/{agent.id}/save-as-template",
        json={"name": "偷"},
        headers=stranger_headers,
    )
    # 016：他人资源与「不存在」同响应（此前是 403，与模板的 404 不对称）
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_audit_rows_written_for_template_actions(
    client, db_session, auth_headers
):
    headers, owner = await auth_headers()
    agent = await _agent(db_session, owner.id)
    await db_session.commit()

    created = await client.post(
        f"/api/v1/agents/{agent.id}/save-as-template",
        json={"name": "审计"},
        headers=headers,
    )
    assert created.status_code == 201
    tpl_id = created.json()["id"]

    patched = await client.patch(
        f"/api/v1/agent-templates/{tpl_id}",
        json={"description": "d"},
        headers=headers,
    )
    assert patched.status_code == 200

    deleted = await client.delete(f"/api/v1/agent-templates/{tpl_id}", headers=headers)
    assert deleted.status_code == 204

    rows = (
        (
            await db_session.execute(
                select(AuditLog.action).where(AuditLog.user_id == owner.id)
            )
        )
        .scalars()
        .all()
    )
    actions = set(rows)
    assert "agent_template_create" in actions
    assert "agent_template_update" in actions
    assert "agent_template_delete" in actions
