"""策略解析单元测试（T052）：direct/fan_out 展开逻辑（US4）。

- direct：展开 1 个执行目标
- fan_out：展开组内全部 active Agent（按 position 排序）
- pipeline / router：显式抛「未实现」错误（Phase 1 保留入口）
"""

from dataclasses import dataclass, field

import pytest

from app.orchestrator.strategies import (
    NotImplementedStrategyError,
    StrategyResolver,
    Target,
)

# 用轻量 dataclass 模拟 DB 对象，保持纯单元测试（不依赖 DB）


@dataclass
class FakeAgent:
    id: str
    status: str = "active"
    name: str = "agent"


@dataclass
class FakeMember:
    """模拟 GroupMember（agent relationship 已预加载）。"""

    agent: FakeAgent
    position: int = 1


@dataclass
class FakeGroup:
    id: str
    routing_strategy: str = "fan_out"
    members: list[FakeMember] = field(default_factory=list)


def _group_with_members(agent_ids: list[str]) -> FakeGroup:
    return FakeGroup(
        id="g1",
        members=[
            FakeMember(agent=FakeAgent(id=a, status="active"), position=i + 1)
            for i, a in enumerate(agent_ids)
        ],
    )


def _group_with_archived(active_ids: list[str], archived_ids: list[str]) -> FakeGroup:
    members = [
        FakeMember(agent=FakeAgent(id=a, status="active"), position=i + 1)
        for i, a in enumerate(active_ids)
    ]
    offset = len(active_ids)
    members += [
        FakeMember(agent=FakeAgent(id=a, status="archived"), position=offset + i + 1)
        for i, a in enumerate(archived_ids)
    ]
    return FakeGroup(id="g1", members=members)


# ---------- direct ----------


def test_direct_expands_single_target():
    """direct → 1 个执行目标（agent）。"""
    targets = StrategyResolver.expand(
        strategy="direct", assignee_type="agent", assignee_id="a1", group=None
    )
    assert targets == [Target(assignee_type="agent", assignee_id="a1")]


def test_direct_ignores_group_members():
    """direct 即使传了组也只用 assignee_id（不展开组员）。"""
    group = _group_with_members(["a1", "a2"])
    targets = StrategyResolver.expand(
        strategy="direct", assignee_type="group", assignee_id="g1", group=group
    )
    assert targets == [Target(assignee_type="group", assignee_id="g1")]


# ---------- fan_out ----------


def test_fan_out_expands_all_active_members_sorted():
    """fan_out → 展开组内全部 active Agent（按 position 升序）。"""
    group = _group_with_members(["c", "a", "b"])
    targets = StrategyResolver.expand(
        strategy="fan_out", assignee_type="group", assignee_id="g1", group=group
    )
    assert [t.assignee_id for t in targets] == ["c", "a", "b"]
    assert all(t.assignee_type == "agent" for t in targets)


def test_fan_out_skips_archived_members():
    """fan_out → 归档成员被排除。"""
    group = _group_with_archived(active_ids=["a1", "a2"], archived_ids=["zz"])
    targets = StrategyResolver.expand(
        strategy="fan_out", assignee_type="group", assignee_id="g1", group=group
    )
    assert [t.assignee_id for t in targets] == ["a1", "a2"]


def test_fan_out_empty_group_raises():
    """fan_out 空组 → 抛 ValueError（无执行目标）。"""
    group = FakeGroup(id="g1", members=[])
    with pytest.raises(ValueError, match="无执行目标"):
        StrategyResolver.expand(
            strategy="fan_out", assignee_type="group", assignee_id="g1", group=group
        )


def test_fan_out_all_archived_raises():
    """fan_out 组内全部归档 → 抛 ValueError。"""
    group = _group_with_archived(active_ids=[], archived_ids=["a1", "a2"])
    with pytest.raises(ValueError, match="无执行目标"):
        StrategyResolver.expand(
            strategy="fan_out", assignee_type="group", assignee_id="g1", group=group
        )


# ---------- 未实现策略 ----------


@pytest.mark.parametrize("strategy", ["pipeline", "router"])
def test_unimplemented_strategies_raise(strategy: str):
    """pipeline/router → 显式抛「未实现」（Phase 1 保留入口）。"""
    with pytest.raises(NotImplementedStrategyError, match="未实现"):
        StrategyResolver.expand(
            strategy=strategy, assignee_type="agent", assignee_id="a1", group=None
        )


def test_unknown_strategy_raises():
    """未知策略 → ValueError。"""
    with pytest.raises(ValueError, match="未知策略"):
        StrategyResolver.expand(
            strategy="teleport", assignee_type="agent", assignee_id="a1", group=None
        )
