"""Agent 规则删除必须与审计共同提交，不依赖数据库的事务接线回归。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy.exc import SQLAlchemyError

from app.api.v1 import agent_egress
from app.core.audit import write_audit


async def test_delete_rule_audits_same_session_before_commit(monkeypatch):
    db = SimpleNamespace(commit=AsyncMock())
    user = SimpleNamespace(id="user-1", role="member")
    rule = SimpleNamespace(
        id="rule-1", target_type="domain", target="api.example.test", port_range="443"
    )
    delete = AsyncMock(return_value=rule)
    audit = AsyncMock()
    calls = Mock()
    calls.attach_mock(delete, "delete")
    calls.attach_mock(audit, "audit")
    calls.attach_mock(db.commit, "commit")
    monkeypatch.setattr(agent_egress.egress_service, "delete_agent_rule", delete)
    monkeypatch.setattr(agent_egress, "write_audit", audit)

    await agent_egress.delete_rule("agent-1", "rule-1", user, db)

    assert [call[0] for call in calls.mock_calls] == ["delete", "audit", "commit"]
    audit.assert_awaited_once_with(
        db,
        user_id="user-1",
        action="egress_rule_delete",
        target_type="agent",
        target_id="agent-1",
        strict=True,
        detail={
            "agent_id": "agent-1",
            "rule_id": "rule-1",
            "target_type": "domain",
            "target": "api.example.test",
            "port_range": "443",
        },
    )


async def test_delete_rule_does_not_commit_after_audit_failure(monkeypatch):
    db = SimpleNamespace(commit=AsyncMock())
    rule = SimpleNamespace(
        id="rule-1", target_type="domain", target="x.test", port_range=None
    )
    monkeypatch.setattr(
        agent_egress.egress_service, "delete_agent_rule", AsyncMock(return_value=rule)
    )
    monkeypatch.setattr(
        agent_egress, "write_audit", AsyncMock(side_effect=SQLAlchemyError("failed"))
    )

    with pytest.raises(SQLAlchemyError):
        await agent_egress.delete_rule(
            "agent-1", "rule-1", SimpleNamespace(id="user-1", role="member"), db
        )
    db.commit.assert_not_awaited()


@pytest.mark.parametrize("strict", [False, True])
async def test_audit_failure_rolls_back_and_raises_only_when_strict(strict):
    error = SQLAlchemyError("audit insert failed")
    db = SimpleNamespace(
        add=Mock(), flush=AsyncMock(side_effect=error), rollback=AsyncMock()
    )
    if strict:
        with pytest.raises(SQLAlchemyError) as caught:
            await write_audit(
                db, user_id="user-1", action="egress_rule_delete", strict=True
            )
        assert caught.value is error
    else:
        await write_audit(db, user_id="user-1", action="existing_caller")
    db.rollback.assert_awaited_once()
