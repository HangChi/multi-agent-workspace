"""状态机单元测试（T061，US5）：Run 状态迁移全路径（FR-015/FR-018/FR-021）。

对齐 data-model.md 状态机：
- queued → running → completed / failed / needs_input → running / cancelled / timeout
- failed 仅可 → queued（重试，重置 current_step/cost/error）
- 终态（completed/failed/cancelled/timeout）不可迁移
- needs_input → running（用户回复继续，FR-021）
- 迁移幂等：重复迁移不抛错

纯单元测试：用轻量 dataclass 模拟 Run 对象（对齐 test_strategies.py 风格）。
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest

from app.orchestrator.state_machine import (
    _RETRYABLE_TERMINAL,
    _TERMINAL,
    RunState,
    RunStatus,
    TransitionError,
    is_terminal,
    transition,
)


@dataclass
class FakeRun:
    """模拟 Run：仅暴露状态机操作所需字段。"""

    id: str = "r1"
    status: str = "queued"
    current_step: int = 0
    cost_cents: int = 0
    error: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    _events: list[str] = field(default_factory=list)

    def mark_started(self) -> None:
        self.started_at = datetime.now(UTC)

    def mark_finished(self) -> None:
        self.finished_at = datetime.now(UTC)

    def clear_execution(self) -> None:
        """failed → queued 重试时重置执行现场。"""
        self.current_step = 0
        self.cost_cents = 0
        self.error = None
        self.started_at = None
        self.finished_at = None


# ---------- 枚举与终态判定 ----------


def test_run_status_enum_contains_all_states():
    """RunStatus 枚举覆盖状态机全部 8 态（006 增加 retrying）。"""
    assert {s.value for s in RunStatus} == {
        "queued",
        "running",
        "needs_input",
        "retrying",
        "completed",
        "failed",
        "cancelled",
        "timeout",
    }


def test_terminal_states():
    """终态：completed/failed/cancelled/timeout；非终态：queued/running/needs_input。"""
    for s in ("completed", "failed", "cancelled", "timeout"):
        assert is_terminal(s) is True
    for s in ("queued", "running", "needs_input"):
        assert is_terminal(s) is False


# ---------- 合法迁移全路径 ----------


def test_queued_to_running_marks_started():
    """queued → running：标记 started_at。"""
    run = FakeRun(status="queued")
    transition(run, RunStatus.RUNNING)
    assert run.status == "running"
    assert run.started_at is not None


def test_running_to_completed_marks_finished():
    """running → completed：标记 finished_at。"""
    run = FakeRun(status="running", started_at=datetime.now(UTC))
    transition(run, RunStatus.COMPLETED)
    assert run.status == "completed"
    assert run.finished_at is not None


def test_running_to_failed():
    """running → failed。"""
    run = FakeRun(status="running", started_at=datetime.now(UTC))
    transition(run, RunStatus.FAILED)
    assert run.status == "failed"


def test_running_to_needs_input():
    """running → needs_input：挂起等待补充（FR-021）。"""
    run = FakeRun(status="running", started_at=datetime.now(UTC))
    transition(run, RunStatus.NEEDS_INPUT)
    assert run.status == "needs_input"


def test_needs_input_to_running():
    """needs_input → running：用户回复后继续（FR-021）。"""
    run = FakeRun(status="needs_input")
    transition(run, RunStatus.RUNNING)
    assert run.status == "running"


def test_running_to_cancelled():
    """running → cancelled：用户取消（FR-018）。"""
    run = FakeRun(status="running", started_at=datetime.now(UTC))
    transition(run, RunStatus.CANCELLED)
    assert run.status == "cancelled"
    assert run.finished_at is not None


def test_queued_to_cancelled():
    """queued → cancelled：排队中也可取消（FR-018）。"""
    run = FakeRun(status="queued")
    transition(run, RunStatus.CANCELLED)
    assert run.status == "cancelled"


def test_running_to_timeout():
    """running → timeout：超时终止。"""
    run = FakeRun(status="running", started_at=datetime.now(UTC))
    transition(run, RunStatus.TIMEOUT)
    assert run.status == "timeout"
    assert run.finished_at is not None


# ---------- failed 重试（重置执行现场） ----------


def test_failed_to_queued_resets_execution_state():
    """failed → queued：仅此一条合法出口，且重置 current_step/cost/error/时间戳（FR-017 重试）。"""
    run = FakeRun(
        status="failed",
        current_step=5,
        cost_cents=123,
        error="LLM 调用失败",
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
    )
    transition(run, RunStatus.QUEUED)
    assert run.status == "queued"
    assert run.current_step == 0
    assert run.cost_cents == 0
    assert run.error is None
    assert run.started_at is None
    assert run.finished_at is None


def test_timeout_to_queued_allowed():
    """timeout → queued：超时也可重试（FR-017 仅 failed/timeout）。"""
    run = FakeRun(status="timeout")
    transition(run, RunStatus.QUEUED)
    assert run.status == "queued"


# ---------- 非法迁移全部拒绝 ----------


@pytest.mark.parametrize("status", ["queued", "running", "needs_input"])
def test_completed_cannot_migrate(status: str):
    """终态 completed 不可再迁移（含进入 needs_input）。"""
    run = FakeRun(status="completed")
    with pytest.raises(TransitionError, match="不可迁移"):
        transition(run, RunStatus(status))


@pytest.mark.parametrize("status", ["queued", "running", "needs_input"])
def test_cancelled_cannot_migrate(status: str):
    """终态 cancelled 不可再迁移。"""
    run = FakeRun(status="cancelled")
    with pytest.raises(TransitionError, match="不可迁移"):
        transition(run, RunStatus(status))


@pytest.mark.parametrize("status", ["queued", "running", "needs_input"])
def test_timeout_cannot_migrate_except_queued(status: str):
    """timeout 仅可 → queued（重试），其余拒绝。"""
    run = FakeRun(status="timeout")
    if status == "queued":
        transition(run, RunStatus.QUEUED)
        assert run.status == "queued"
    else:
        with pytest.raises(TransitionError, match="不可迁移"):
            transition(run, RunStatus(status))


@pytest.mark.parametrize("to", ["completed", "timeout", "needs_input"])
def test_queued_cannot_skip_to_other_states(to: str):
    """queued 仅可 → running/cancelled，其余拒绝（不可跳过 running 直接完成）。"""
    run = FakeRun(status="queued")
    with pytest.raises(TransitionError, match="不可迁移"):
        transition(run, RunStatus(to))


@pytest.mark.parametrize(
    "to", ["completed", "needs_input", "cancelled", "timeout", "failed"]
)
def test_running_restricted_exits(to: str):
    """running → completed/failed/needs_input/cancelled/timeout 全为合法；回到 queued 才非法。"""
    run = FakeRun(status="running")
    transition(run, RunStatus(to))
    assert run.status == to


def test_running_cannot_go_back_to_queued():
    """running → queued：非法（回退仅限 failed/timeout 重试）。"""
    run = FakeRun(status="running")
    with pytest.raises(TransitionError, match="不可迁移"):
        transition(run, RunStatus.QUEUED)


@pytest.mark.parametrize("to", ["queued", "completed", "timeout", "failed"])
def test_needs_input_only_back_to_running(to: str):
    """needs_input 仅可 → running（回复继续）/cancelled（放弃挂起），其余拒绝。"""
    run = FakeRun(status="needs_input")
    with pytest.raises(TransitionError, match="不可迁移"):
        transition(run, RunStatus(to))


def test_needs_input_can_cancel():
    """needs_input → cancelled：用户放弃挂起任务（FR-018）。"""
    run = FakeRun(status="needs_input")
    transition(run, RunStatus.CANCELLED)
    assert run.status == "cancelled"


# ---------- 幂等 ----------


def test_transition_to_same_status_is_noop():
    """迁移到当前状态：幂等 no-op（不抛错、不更新时间戳）。"""
    run = FakeRun(status="running", started_at=datetime.now(UTC))
    finished_before = run.finished_at
    transition(run, RunStatus.RUNNING)
    assert run.status == "running"
    assert run.finished_at == finished_before


# ---------- retrying：自动重试的中间态（006） ----------


def test_running_to_retrying_is_legal():
    """瞬时失败 → 进入重试等待（006 / FR-016）。"""
    run = FakeRun(status="running")
    transition(run, RunStatus.RETRYING)
    assert run.status == "retrying"


def test_retrying_to_running_is_legal():
    """重试到点 → 重新开始执行。"""
    run = FakeRun(status="retrying")
    transition(run, RunStatus.RUNNING)
    assert run.status == "running"


def test_retrying_to_failed_is_legal():
    """重试次数耗尽 → 终态 failed（FR-009）。"""
    run = FakeRun(status="retrying")
    transition(run, RunStatus.FAILED)
    assert run.status == "failed"


def test_retrying_is_not_terminal():
    """retrying 是中间态，必须允许继续迁移（否则重试机制整个失效）。"""
    assert RunStatus.RETRYING not in _TERMINAL
    assert RunStatus.RETRYING not in _RETRYABLE_TERMINAL


def test_retrying_cannot_skip_to_completed():
    """retrying 不能直接到 completed：必须先 running，否则跳过了实际执行。"""
    run = FakeRun(status="retrying")
    with pytest.raises(TransitionError):
        transition(run, RunStatus.COMPLETED)


def test_retrying_cannot_go_back_to_queued():
    """retrying 不允许回 queued：那会绕过 attempt 计数与退避调度。"""
    run = FakeRun(status="retrying")
    with pytest.raises(TransitionError):
        transition(run, RunStatus.QUEUED)


def test_retrying_cannot_cancel():
    """取消只能从 running/queued/needs_input 发起；等待重试中的任务
    已经在终态路径上，再引入一条取消入口会让「谁撤销了重试」说不清。"""
    run = FakeRun(status="retrying")
    with pytest.raises(TransitionError):
        transition(run, RunStatus.CANCELLED)


def test_transition_to_retrying_is_idempotent():
    run = FakeRun(status="retrying")
    transition(run, RunStatus.RETRYING)
    assert run.status == "retrying"


# ---------- 015 覆盖率补齐：终态判定与执行现场重置 ----------


@pytest.mark.parametrize(
    "status",
    ["completed", "failed", "timeout", "cancelled"],
)
def test_is_terminal_covers_all_four_terminal_states(status):
    """`is_terminal` 把 failed/timeout 也算终态（虽可重试，但本轮已结束）。

    这条区分很关键：`transition` 用同一个判据决定要不要打 finished_at 并
    计数 RUN_TERMINAL。漏掉 failed/timeout 会让看板的「失败率」少一半。
    """
    assert is_terminal(status) is True
    assert is_terminal(RunStatus(status)) is True


@pytest.mark.parametrize("status", ["queued", "running", "needs_input", "retrying"])
def test_is_terminal_rejects_non_terminal_states(status):
    assert is_terminal(status) is False


def test_clear_execution_resets_every_execution_field():
    """failed → queued 手动重试时必须把执行现场清干净。

    漏清 `error` 会让重试后的详情页仍显示上一次的失败原因；漏清 cost 会让
    重试被当成「已花钱」而立刻触发成本上限——两者都是静默的数据错误。
    """
    run = RunState(
        status="failed",
        current_step=7,
        cost_cents=42,
        cost_micros=42_000_000,
        error="上次失败了",
        result={"answer": "旧结果"},
    )
    run.mark_started()
    run.mark_finished()

    run.clear_execution()

    assert run.current_step == 0
    assert run.cost_cents == 0
    assert run.cost_micros == 0
    assert run.error is None
    assert run.result is None
    assert run.started_at is None
    assert run.finished_at is None
