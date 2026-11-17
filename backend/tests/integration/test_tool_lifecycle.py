"""Enabled-tool policy and immutable run grant history against PostgreSQL."""

import uuid

import pytest
from sqlalchemy import select

from app.executors.catalog import CATALOG
from app.executors.llm import LLMResponse, ToolCall
from app.models.agent import Agent
from app.models.audit_log import AuditLog
from app.models.run import Run
from app.models.step import Step
from app.models.tool import Tool
from app.tasks.execute_run import _execute


@pytest.fixture
async def enabled_catalog(db_session):
    """Restore operator switches even when a regression assertion fails."""
    for spec in CATALOG.values():
        tool = await db_session.get(Tool, spec.name)
        if tool is None:
            tool = Tool(
                name=spec.name,
                display_name=spec.display_name,
                description=spec.description,
                kind=spec.kind,
                param_schema=spec.param_schema,
                timeout_seconds=spec.timeout_seconds,
            )
            db_session.add(tool)
        tool.enabled = True
    await db_session.commit()
    yield
    db_session.expire_all()  # Operator changes may have used a separate session.
    for name in CATALOG:
        tool = await db_session.get(Tool, name)
        if tool is None:
            spec = CATALOG[name]
            db_session.add(
                Tool(
                    name=name,
                    display_name=spec.display_name,
                    description=spec.description,
                    kind=spec.kind,
                    param_schema=spec.param_schema,
                    timeout_seconds=spec.timeout_seconds,
                    enabled=True,
                )
            )
        else:
            tool.enabled = True
    await db_session.commit()


async def _agent(client, headers, tools):
    response = await client.post(
        "/api/v1/agents",
        headers=headers,
        json={
            "name": f"policy-{uuid.uuid4().hex[:8]}",
            "model": "gpt-4o-mini",
            "system_prompt": "Follow tool grants.",
            "tools": tools,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _assign(client, headers, agent_id):
    request = await client.post(
        "/api/v1/requests",
        headers=headers,
        json={
            "title": "Grant history",
            "priority": "normal",
        },
    )
    assert request.status_code == 201, request.text
    response = await client.post(
        f"/api/v1/requests/{request.json()['id']}/assign",
        headers=headers,
        json={"assignee_type": "agent", "assignee_id": agent_id},
    )
    assert response.status_code == 202, response.text
    return response.json()["run_id"]


@pytest.mark.asyncio
@pytest.mark.usefixtures("enabled_catalog")
async def test_disabled_tool_hidden_and_rejected_on_create_and_update(
    client, make_headers, db_session
):
    headers = await make_headers()
    agent = await _agent(client, headers, ["web_search"])
    tool = await db_session.get(Tool, "calculate")
    tool.enabled = False
    await db_session.commit()

    catalog = await client.get("/api/v1/tools", headers=headers)
    assert catalog.status_code == 200
    assert "calculate" not in {item["name"] for item in catalog.json()["items"]}
    for method, path in [
        ("post", "/api/v1/agents"),
        ("patch", f"/api/v1/agents/{agent['id']}"),
    ]:
        response = await getattr(client, method)(
            path,
            headers=headers,
            json={
                "name": "disabled-grant",
                "system_prompt": "Check grants.",
                "tools": ["calculate"],
            },
        )
        assert response.status_code == 400, response.text
        detail = response.json()["error"]["message"]
        assert "calculate" in detail and "web_search" in detail
    await db_session.refresh(await db_session.get(Agent, agent["id"]))
    assert (await db_session.get(Agent, agent["id"])).tools == ["web_search"]


@pytest.mark.asyncio
@pytest.mark.usefixtures("enabled_catalog")
async def test_missing_tool_row_fails_closed(client, make_headers, db_session):
    headers = await make_headers()
    tool = await db_session.get(Tool, "run_shell")
    await db_session.delete(tool)
    await db_session.commit()
    catalog = await client.get("/api/v1/tools", headers=headers)
    assert "run_shell" not in {item["name"] for item in catalog.json()["items"]}
    response = await client.post(
        "/api/v1/agents",
        headers=headers,
        json={
            "name": "missing-grant",
            "system_prompt": "Check grants.",
            "tools": ["run_shell"],
        },
    )
    assert response.status_code == 400


@pytest.mark.asyncio
@pytest.mark.usefixtures("enabled_catalog")
async def test_assignment_snapshots_grants_and_history_survives_agent_edit(
    client, make_headers, db_session
):
    headers = await make_headers()
    agent = await _agent(client, headers, ["calculate", "web_search"])
    run_id = await _assign(client, headers, agent["id"])
    response = await client.get(f"/api/v1/runs/{run_id}", headers=headers)
    assert response.status_code == 200
    assert response.json()["tool_grants_snapshot"] == ["calculate", "web_search"]
    updated = await client.patch(
        f"/api/v1/agents/{agent['id']}", headers=headers, json={"tools": []}
    )
    assert updated.status_code == 200
    response = await client.get(f"/api/v1/runs/{run_id}", headers=headers)
    assert response.json()["tool_grants_snapshot"] == ["calculate", "web_search"]
    run = await db_session.get(Run, run_id)
    run.clear_execution()
    assert run.tool_grants_snapshot == ["calculate", "web_search"]


@pytest.mark.asyncio
@pytest.mark.usefixtures("enabled_catalog")
async def test_worker_denies_disabled_stale_grant_without_rewriting_history(
    client,
    make_headers,
    db_session,
    monkeypatch,
):
    headers = await make_headers()
    agent_data = await _agent(client, headers, ["calculate"])
    run_id = await _assign(client, headers, agent_data["id"])
    tool = await db_session.get(Tool, "calculate")
    tool.enabled = False
    await db_session.commit()
    # Simulate a stale configuration read / legacy assignment that predates disable.
    agent = await db_session.get(Agent, agent_data["id"])
    agent.tools = ["calculate"]
    await db_session.commit()
    replies = iter(
        [
            LLMResponse(
                tool_calls=[ToolCall(name="calculate", arguments={"expression": "2+2"})]
            ),
            LLMResponse(content="No granted tool was executed."),
        ]
    )
    schemas = []

    async def chat(_self, messages, **kwargs):
        schemas.extend(kwargs.get("tools") or [])
        return next(replies)

    monkeypatch.setattr("app.tasks.execute_run.LiteLLMClient.chat", chat)
    run = await db_session.get(Run, run_id)
    await _execute(db_session, run, agent)
    assert run.status == "completed", run.error
    assert not schemas
    assert agent.tools == []
    assert run.tool_grants_snapshot == ["calculate"]
    steps = (
        (await db_session.execute(select(Step).where(Step.run_id == run_id)))
        .scalars()
        .all()
    )
    denied = [step for step in steps if step.tool_name == "calculate"]
    assert len(denied) == 1 and denied[0].status == "failed"
    audit = (
        (
            await db_session.execute(
                select(AuditLog).where(
                    AuditLog.target_id == run_id,
                    AuditLog.action == "tool_denied",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(audit) == 1


@pytest.mark.asyncio
@pytest.mark.usefixtures("enabled_catalog")
async def test_worker_does_not_grant_tools_added_after_assignment(
    client, make_headers, db_session, monkeypatch
):
    headers = await make_headers()
    agent_data = await _agent(client, headers, [])
    run_id = await _assign(client, headers, agent_data["id"])
    updated = await client.patch(
        f"/api/v1/agents/{agent_data['id']}",
        headers=headers,
        json={"tools": ["calculate"]},
    )
    assert updated.status_code == 200
    replies = iter(
        [
            LLMResponse(
                tool_calls=[ToolCall(name="calculate", arguments={"expression": "2+2"})]
            ),
            LLMResponse(content="No escalation."),
        ]
    )

    async def chat(_self, messages, **kwargs):
        assert not kwargs.get("tools")
        return next(replies)

    monkeypatch.setattr("app.tasks.execute_run.LiteLLMClient.chat", chat)
    run = await db_session.get(Run, run_id)
    agent = await db_session.get(Agent, agent_data["id"])
    await _execute(db_session, run, agent)
    assert run.status == "completed", run.error
    assert run.tool_grants_snapshot == []


@pytest.mark.asyncio
@pytest.mark.usefixtures("enabled_catalog")
async def test_disable_during_llm_turn_is_checked_before_dispatch(
    client, make_headers, db_session, monkeypatch
):
    from tests.conftest import TestSession

    headers = await make_headers()
    agent_data = await _agent(client, headers, ["calculate"])
    run_id = await _assign(client, headers, agent_data["id"])
    calls = 0

    async def chat(_self, messages, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            assert kwargs.get("tools")
            async with TestSession() as operator_db:
                tool = await operator_db.get(Tool, "calculate")
                tool.enabled = False
                await operator_db.commit()
            return LLMResponse(
                tool_calls=[ToolCall(name="calculate", arguments={"expression": "2+2"})]
            )
        assert not kwargs.get("tools")
        return LLMResponse(content="Disabled call denied.")

    monkeypatch.setattr("app.tasks.execute_run.LiteLLMClient.chat", chat)
    run = await db_session.get(Run, run_id)
    agent = await db_session.get(Agent, agent_data["id"])
    await _execute(db_session, run, agent)
    assert run.status == "completed", run.error
    step = (
        await db_session.execute(
            select(Step).where(
                Step.run_id == run_id,
                Step.tool_name == "calculate",
            )
        )
    ).scalar_one()
    assert step.output["denied"] is True
    assert step.status == "failed"
    assert run.tool_grants_snapshot == ["calculate"]


@pytest.mark.asyncio
@pytest.mark.usefixtures("enabled_catalog")
async def test_first_known_grants_are_committed_before_awaiting_llm(
    client,
    make_headers,
    db_session,
    monkeypatch,
):
    from tests.conftest import TestSession

    headers = await make_headers()
    agent_data = await _agent(client, headers, ["calculate"])
    agent = await db_session.get(Agent, agent_data["id"])
    request = await client.post(
        "/api/v1/requests",
        headers=headers,
        json={
            "title": "Unrecorded pending legacy grants",
            "priority": "normal",
        },
    )
    assert request.status_code == 201
    # A pending pre-migration run genuinely has no grant history yet. Its first
    # execution can record known grants, without reconstructing completed history.
    run = Run(
        request_id=request.json()["id"],
        owner_id=agent.owner_id,
        assignee_type="agent",
        assignee_id=agent.id,
        strategy="direct",
        status="queued",
    )
    db_session.add(run)
    await db_session.commit()
    observed = []

    async def chat(_self, messages, **kwargs):
        async with TestSession() as reader:
            observed.append(
                await reader.scalar(
                    select(Run.tool_grants_snapshot).where(Run.id == run.id)
                )
            )
        return LLMResponse(content="Known configuration recorded before inference.")

    monkeypatch.setattr("app.tasks.execute_run.LiteLLMClient.chat", chat)
    await _execute(db_session, run, agent)
    assert run.status == "completed", run.error
    assert observed == [["calculate"]]
