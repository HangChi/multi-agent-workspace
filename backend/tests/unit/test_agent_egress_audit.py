"""Agent 规则删除必须与审计共同提交，不依赖数据库的事务接线回归。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy.exc import SQLAlchemyError

from app.api.errors import AppError
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


@pytest.mark.parametrize("strict", [None, True, False])
async def test_audit_failure_rolls_back_and_raises_unless_opted_out(strict):
    """审计与业务同事务：默认（strict 未显式关闭）必须回滚并上报。

    docs/code-audit-2026-11-17.md #9 的合并事务前提是「审计失败 ⇒ 业务失败」，
    否则端点会在业务已回滚的情况下仍返回 200。strict=False 是唯一的逃生口，
    只留给「审计写在独立事务里」的场景。
    """
    error = SQLAlchemyError("audit insert failed")
    db = SimpleNamespace(
        add=Mock(), flush=AsyncMock(side_effect=error), rollback=AsyncMock()
    )
    kwargs = {} if strict is None else {"strict": strict}
    if strict is False:
        await write_audit(db, user_id="user-1", action="existing_caller", **kwargs)
    else:
        with pytest.raises(AppError) as caught:
            await write_audit(
                db, user_id="user-1", action="egress_rule_delete", **kwargs
            )
        assert caught.value.code == "INTERNAL_ERROR"
    db.rollback.assert_awaited_once()
