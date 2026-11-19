"""需求状态机单元测试（T009，US1/US2/US6）：迁移表全路径 + 不变量。

对齐 data-model.md §2 与 contracts §3：
- created → pending（分派）/ assigned / running（拖给 agent，既有链路直达）
- pending → accepted（接收）/ pending（转交幂等）/ created（退回）
- accepted → pending（转交）/ running（拖给 agent）/ created（退回）
- running → completed / failed / cancelled（既有 settle 与 reaper）
- 终态（completed/failed/cancelled）不可迁移；assigned 及以后拒收分派类操作

纯单元测试：轻量 dataclass 模拟 Request（对齐 test_state_machine.py 风格）。
"""

from dataclasses import dataclass

import pytest

from app.orchestrator.request_state_machine import (
    AGENT_ASSIGNABLE_STATUSES,
    DISPATCHABLE_STATUSES,
    RequestStatus,
    TransitionError,
    assert_dispatch_invariants,
    can_transition,
    is_terminal,
    transition,
)


@dataclass
class FakeRequest:
    """模拟 Request：仅暴露状态机操作所需字段。"""

    id: str = "req-1"
    status: str = "created"
    assignee_id: str | None = None
    dispatched_at: object = None
    accepted_at: object = None


# ---------- 枚举与终态 ----------


def test_request_status_enum_contains_all_states():
    """RequestStatus 覆盖 8 态（007 增加 pending/accepted）。"""
    assert {s.value for s in RequestStatus} == {
        "created",
        "pending",
        "accepted",
        "assigned",
        "running",
        "completed",
        "failed",
        "cancelled",
    }


def test_terminal_states():
    """终态：completed/failed/cancelled。"""
    for s in ("completed", "failed", "cancelled"):
        assert is_terminal(s) is True
    for s in ("created", "pending", "accepted", "assigned", "running"):
        assert is_terminal(s) is False


# ---------- 迁移表：合法路径 ----------


@pytest.mark.parametrize(
    ("from_status", "to_status"),
    [
        ("created", "pending"),  # 分派给员工
        ("created", "assigned"),  # 遗留中间态（枚举保留，兼容旧数据）
        ("created", "running"),  # 拖给自己的 agent（既有链路直达，无 assigned 中间写）
        ("pending", "accepted"),  # 员工接收
        ("pending", "pending"),  # 转交（同态，服务层换接收人）
        ("pending", "created"),  # 退回
        ("accepted", "pending"),  # 转交给同事
        ("accepted", "running"),  # 接收后拖给 agent（3b 新增准入）
        ("accepted", "created"),  # 接收后退回
        ("assigned", "running"),  # 遗留：已分配瞬态入队
        ("running", "completed"),
        ("running", "failed"),
        ("running", "cancelled"),
    ],
)
def test_legal_transitions(from_status, to_status):
    req = FakeRequest(status=from_status)
    assert transition(req, RequestStatus(to_status)) == RequestStatus(to_status)
    assert req.status == to_status


# ---------- 迁移表：非法路径 ----------


@pytest.mark.parametrize(
    ("from_status", "to_status"),
    [
        ("pending", "running"),  # 未接收不能执行（US3 第三条）
        ("pending", "assigned"),
        ("pending", "completed"),  # 不能跳过执行直接终态
        ("accepted", "completed"),
        ("running", "pending"),  # 执行中不可分派（FR-005）
        ("running", "accepted"),
        ("running", "created"),
        ("completed", "created"),
        ("completed", "running"),
        ("failed", "pending"),
        ("cancelled", "accepted"),
        ("assigned", "pending"),  # 已入队不可分派（FR-005）
        ("assigned", "created"),
    ],
)
def test_illegal_transitions_raise(from_status, to_status):
    req = FakeRequest(status=from_status)
    with pytest.raises(TransitionError):
        transition(req, RequestStatus(to_status))
    assert req.status == from_status  # 失败不改变状态


# ---------- 幂等 ----------


def test_transition_to_same_status_is_noop():
    """同态迁移是 no-op（调用方据此实现幂等，不报错）。

    重复 accept 的 409 由 service 层在调 transition 前按「当前状态必须为
    pending」拦截（AC-4 集成用例），状态机本身同态放行——settle 的
    running→running 依赖同一语义。
    """
    req = FakeRequest(status="pending", assignee_id="u1")
    assert transition(req, RequestStatus.PENDING) == RequestStatus.PENDING
    assert req.assignee_id == "u1"


def test_transition_from_running_to_running_is_noop():
    """settle 重复调用（两个 Run 同时结束）不得抛错。"""
    req = FakeRequest(status="running")
    assert transition(req, RequestStatus.RUNNING) == RequestStatus.RUNNING


# ---------- 可操作集合 ----------


def test_dispatchable_statuses():
    """分派类操作（dispatch/accept/reject/reassign）准入：三态。"""
    assert DISPATCHABLE_STATUSES == frozenset(
        {RequestStatus.CREATED, RequestStatus.PENDING, RequestStatus.ACCEPTED}
    )


def test_agent_assignable_statuses():
    """拖给 agent 准入：created/assigned/accepted（3b 新增 accepted）。"""
    assert AGENT_ASSIGNABLE_STATUSES == frozenset(
        {RequestStatus.CREATED, RequestStatus.ASSIGNED, RequestStatus.ACCEPTED}
    )


# ---------- 不变量 ----------


def test_invariant_assignee_presence():
    """assignee_id 非空 ⇔ 状态 ∈ {pending, accepted}。"""
    ok = FakeRequest(status="pending", assignee_id="u1")
    assert_dispatch_invariants(ok)  # 不抛错
    bad = FakeRequest(status="pending", assignee_id=None)
    with pytest.raises(ValueError, match="assignee_id"):
        assert_dispatch_invariants(bad)
    stray = FakeRequest(status="running", assignee_id="u1")
    assert_dispatch_invariants(stray)  # 执行中保留经手人快照，合法


def test_invariant_accepted_at_presence():
    """accepted_at 非空 ⇔ 状态 = accepted。"""
    ok = FakeRequest(status="accepted", assignee_id="u1", accepted_at="ts")
    assert_dispatch_invariants(ok)
    bad = FakeRequest(status="accepted", assignee_id="u1", accepted_at=None)
    with pytest.raises(ValueError, match="accepted_at"):
        assert_dispatch_invariants(bad)
    stale = FakeRequest(status="pending", assignee_id="u1", accepted_at="ts")
    with pytest.raises(ValueError, match="accepted_at"):
        assert_dispatch_invariants(stale)


def test_invariant_created_has_no_assignee():
    """created（退回后）不得残留 assignee/accepted_at。"""
    bad = FakeRequest(status="created", assignee_id="u1")
    with pytest.raises(ValueError, match="assignee_id"):
        assert_dispatch_invariants(bad)


# ---------- 015 覆盖率补齐：can_transition 预判 ----------


@pytest.mark.parametrize(
    ("current", "target"),
    [
        ("created", "pending"),
        ("pending", "accepted"),
        ("accepted", "running"),
        ("running", "completed"),
        # created → running 是既有链路的真实路径（assign 端点直达，
        # 中间不落 assigned），不是「未分派不能跑」
        ("created", "running"),
    ],
)
def test_can_transition_allows_legal_moves(current, target):
    """`can_transition` 是「只问不迁移」的预判口。

    服务层在写分派历史、发通知之前先问一句——否则非法迁移会在写完历史后
    才抛 409，留下半截数据。所以它与 `transition` 的判据必须完全一致。
    """
    assert can_transition(current, target) is True
    # 枚举入参同样成立（服务层两种写法都有）
    assert can_transition(RequestStatus(current), RequestStatus(target)) is True


@pytest.mark.parametrize(
    ("current", "target"),
    [
        ("pending", "running"),  # 必须先接收（accepted）才能拖给 agent
        ("completed", "pending"),  # 终态不可回退
        ("cancelled", "accepted"),
        # failed 也是终态：需求级没有「重试离开终态」规则，重试是 Run 级概念
        ("failed", "cancelled"),
    ],
)
def test_can_transition_rejects_illegal_moves(current, target):
    assert can_transition(current, target) is False


@pytest.mark.parametrize("status", ["created", "pending", "running"])
def test_can_transition_to_self_is_always_true(status):
    """迁移到自身是 no-op 而非错误：调用方无需为「已经是目标状态」加特判。"""
    assert can_transition(status, status) is True
