"""Run 状态机（T065，US5）：合法迁移表 + 幂等迁移函数（FR-015/FR-018/FR-021）。

对齐 data-model.md 状态机：
- queued → running → completed / failed / needs_input → running / cancelled / timeout
- failed / timeout → queued（重试，FR-017；重置执行现场）
- 终态（completed/failed/cancelled/timeout）不可迁移
- needs_input → running（用户回复继续，FR-021）/ cancelled（用户放弃挂起任务，FR-018）
- 迁移幂等：重复迁移 no-op

纯函数式实现：不依赖 DB，run 对象由调用方传入（ORM 或测试 dataclass）。
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Protocol


class RunStatus(str, Enum):
    """Run 状态机全部状态（对齐 run.status 枚举）。"""

    QUEUED = "queued"
    RUNNING = "running"
    NEEDS_INPUT = "needs_input"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"


# 终态：不可再迁移（除非 failed/timeout → queued 重试，重试按「离开终态」单独规则）
_TERMINAL = frozenset({RunStatus.COMPLETED, RunStatus.CANCELLED})
_RETRYABLE_TERMINAL = frozenset({RunStatus.FAILED, RunStatus.TIMEOUT})

# 允许迁移表：from → {to, ...}
_TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    RunStatus.QUEUED: frozenset({RunStatus.RUNNING, RunStatus.CANCELLED}),
    RunStatus.RUNNING: frozenset(
        {
            RunStatus.COMPLETED,
            RunStatus.FAILED,
            RunStatus.NEEDS_INPUT,
            RunStatus.CANCELLED,
            RunStatus.TIMEOUT,
        }
    ),
    RunStatus.NEEDS_INPUT: frozenset({RunStatus.RUNNING, RunStatus.CANCELLED}),
    RunStatus.FAILED: frozenset({RunStatus.QUEUED}),
    RunStatus.TIMEOUT: frozenset({RunStatus.QUEUED}),
    RunStatus.COMPLETED: frozenset(),
    RunStatus.CANCELLED: frozenset(),
}


class TransitionError(ValueError):
    """非法状态迁移。"""


class RunLike(Protocol):
    """迁移函数所需的 Run 最小接口（ORM Run 或测试 dataclass）。"""

    status: str
    current_step: int
    cost_cents: int
    error: str | None
    result: dict[str, Any] | None
    started_at: datetime | None
    finished_at: datetime | None

    def mark_started(self) -> None: ...

    def mark_finished(self) -> None: ...

    def clear_execution(self) -> None: ...


@dataclass
class RunState:
    """纯数据载体：供不需要 ORM 的场景（测试/内存执行）。"""

    status: str = "queued"
    current_step: int = 0
    cost_cents: int = 0
    error: str | None = None
    result: dict[str, Any] | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    _events: list[str] = field(default_factory=list, repr=False)

    def mark_started(self) -> None:
        self.started_at = datetime.now(UTC)

    def mark_finished(self) -> None:
        self.finished_at = datetime.now(UTC)

    def clear_execution(self) -> None:
        self.current_step = 0
        self.cost_cents = 0
        self.error = None
        self.result = None
        self.started_at = None
        self.finished_at = None


def is_terminal(status: str | RunStatus) -> bool:
    """终态判定：completed/cancelled/failed/timeout。"""
    return RunStatus(status) in _TERMINAL | _RETRYABLE_TERMINAL


def transition(run: RunLike, to: RunStatus, *, force: bool = False) -> RunStatus:
    """把 run 从当前状态迁移到 to。

    - 非法迁移 → TransitionError（force=True 时跳过校验，用于 Worker 抢占式恢复）
    - 迁移到当前状态 → no-op（幂等）
    - 进入 running 时标记 started_at；进入终态时标记 finished_at；
      failed/timeout → queued 时重置执行现场（current_step/cost/error/时间戳）
    """
    current = RunStatus(run.status)

    # 幂等：已在目标状态直接返回
    if current == to:
        return to

    allowed = _TRANSITIONS[current]
    if to not in allowed and not force:
        raise TransitionError(f"{current.value} 不可迁移到 {to.value}")

    run.status = to.value

    # 时间戳维护
    if to == RunStatus.RUNNING:
        run.mark_started()
    if is_terminal(to):
        run.mark_finished()
    if to == RunStatus.QUEUED and current in _RETRYABLE_TERMINAL:
        run.clear_execution()

    return to
