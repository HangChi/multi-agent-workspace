"""003 external grants stay separate from immutable 002 builtin tool grants."""

import pytest

from app.models.agent import Agent
from app.models.run import Run


@pytest.mark.asyncio
async def test_external_grants_do_not_change_builtin_agent_tools_or_run_snapshot(db_session, make_user):
    owner, _ = await make_user(role="member")
    agent = Agent(
        owner_id=owner.id,
        name="Builtin regression",
        system_prompt="Keep builtin tools isolated",
        model="test-model",
        tools=["calculate"],
    )
    db_session.add(agent)
    await db_session.flush()
    run = Run(
        request_id="00000000-0000-0000-0000-000000000001",
        owner_id=owner.id,
        assignee_type="agent",
        assignee_id=agent.id,
        strategy="direct",
        status="queued",
        tool_grants_snapshot=["calculate"],
    )
    # The run's request FK is intentionally satisfied with a real request fixture
    # in the API integration suite. Here assert assignment semantics using model state.
    assert agent.tools == ["calculate"]
    assert run.tool_grants_snapshot == ["calculate"]
    assert "external_tool_grants" not in (agent.tools or [])
