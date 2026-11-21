"""`execute_run` Celery 任务壳的策略分支（015 覆盖率补齐）。

任务壳只有几十行，但它承载了四条「坏了没人发现」的策略，且都不在正常
执行路径上：

1. **锁获取失败**：同一 Run 被并发入队时必须直接返回 `busy`，不能重复执行
   （重复执行 = 双倍成本 + steps 表 seq 唯一约束撞车）。
2. **终态后到点的重试消息必须丢弃**：判据用 `status` 而不是 `next_retry_at`，
   因为后者可能因时区/写入顺序偏差；用错判据会让一个已 cancelled 的 Run
   被重新跑起来。
3. **Worker 侧配额兜底**：分配后配额可能已变化，入队不回滚但执行要拦住。
4. **致命错误回收**：`_main` 抛出不可恢复异常时仍要把 Run 落成 failed，
   否则任务死循环、Run 永远卡在 running。

测试手法：`TESTING=1` 会让任务壳在开头直接短路（`conftest` 全局设了它），
所以这里显式清掉该变量，并把 `SessionLocal` / `_acquire_lock` /
`_execute` 换成替身——验的是「判据与返回值」，不是 DB 语义。
"""

import uuid
from dataclasses import dataclass, field

import pytest

from app.orchestrator.state_machine import RunState, RunStatus
from app.services.quota_service import QuotaExceeded
from app.tasks import execute_run as er

pytestmark = pytest.mark.integration


@dataclass
class _FakeRun(RunState):
    """Run 替身：状态机能力直接继承 `RunState`，只补任务壳用到的业务字段。

    为什么不自己写一个 dataclass：`transition()` 会调用 `mark_started` /
    `mark_finished` / `clear_execution`，手写替身漏一个方法就变成
    AttributeError，测的就不是任务壳了。
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    owner_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    attempt: int = 1
    # 任务壳只在 `_load_context` 里读这两个字段决定要不要取 Agent
    assignee_type: str = "agent"
    assignee_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    next_retry_at: object = None
    bumped: int = 0

    def bump_attempt(self) -> None:
        self.attempt += 1
        self.bumped += 1


class _FakeResult:
    """`db.execute(...)` 的返回值。

    需要覆盖三种消费方式，否则用例会因为替身不全而走成失败分支：
    - `.scalars().first()`：任务壳取 Run
    - `.scalar_one()`：配额检查
    - `.scalar()`：`_load_context` 取 max(Step.seq) 作续写起点
    """

    def __init__(self, value) -> None:
        self._value = value

    def scalars(self):
        return self

    def first(self):
        return self._value

    def scalar_one(self):
        return 0

    def scalar(self):
        return 0


class _FakeSession:
    """最小 async session：`async with` + execute/get/refresh/commit。"""

    def __init__(self, run: _FakeRun | None, agent=None, request=None) -> None:
        self.run = run
        self.agent = agent
        # `_load_context` 里 Request 为 None 会直接 RuntimeError，这里给个哨兵
        self.request = request if request is not None else object()
        self.commits = 0
        self.refreshed = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info) -> bool:
        return False

    async def execute(self, _stmt):
        return _FakeResult(self.run)

    async def get(self, model, _pk):
        # `_load_context` 用 get 分别取 Agent 与 Request，致命错误回收路径
        # 用 get 取 Run——按模型名分流，避免三者互相串味。
        name = model.__name__
        if name == "Agent":
            return self.agent
        if name == "Run":
            return self.run
        return self.request

    async def commit(self) -> None:
        self.commits += 1

    async def refresh(self, _obj) -> None:
        self.refreshed += 1


@pytest.fixture
def shell(monkeypatch):
    """让任务壳真正跑起来：清掉 TESTING 短路，锁默认拿得到。

    `_acquire_lock` 返回持有者 token（不再是 bool）；`_release_lock` 收两个
    参数并按 token 做 CAS。替身保持同一形状，顺带记录释放时传的是哪个 token。
    """
    monkeypatch.delenv("TESTING", raising=False)
    monkeypatch.setattr(er, "_acquire_lock", lambda _run_id: "test-token")
    released: list[tuple[str, str | None]] = []
    monkeypatch.setattr(
        er, "_release_lock", lambda run_id, token: released.append((run_id, token))
    )

    def _install(run: _FakeRun | None) -> _FakeSession:
        session = _FakeSession(run)
        monkeypatch.setattr(er, "SessionLocal", lambda: session)
        monkeypatch.setattr(er, "settle_request_status", _no_settle)
        # 配额检查默认放行：绝大多数用例只想验别的事，配额单独一条用例覆盖。
        monkeypatch.setattr(er, "assert_user_quota", _allow_quota)
        return session

    _install.released = released  # type: ignore[attr-defined]
    return _install


async def _no_settle(_db, _request_id) -> bool:
    return False


async def _allow_quota(_db, _owner_id) -> None:
    return None


def test_testing_short_circuit_returns_before_touching_broker(monkeypatch):
    """TESTING=1 必须直接返回：测试会话里绝不能真连 broker/DB。"""

    def _boom(_run_id):
        raise AssertionError("短路应当发生在获取锁之前")

    monkeypatch.setattr(er, "_acquire_lock", _boom)
    assert er.execute_run("any-run") == "skipped-test"


def test_lock_busy_returns_without_loading_run(shell, monkeypatch):
    """拿不到锁说明已有 Worker 在执行——直接返回 busy，不读库。"""
    session = shell(None)
    monkeypatch.setattr(er, "_acquire_lock", lambda _run_id: None)
    assert er.execute_run("run-busy") == "busy"
    assert session.commits == 0


def test_cancelled_run_skips_execution(shell, monkeypatch):
    """加载后已是 cancelled：不执行、不改状态。"""
    run = _FakeRun(status=RunStatus.CANCELLED.value)
    shell(run)
    called: list[str] = []
    monkeypatch.setattr(er, "_execute", lambda *a: called.append("x"))
    assert er.execute_run(run.id) == "cancelled"
    assert called == []


@pytest.mark.parametrize(
    "status",
    [
        RunStatus.COMPLETED.value,
        RunStatus.FAILED.value,
        RunStatus.TIMEOUT.value,
        RunStatus.NEEDS_INPUT.value,
    ],
)
def test_retry_message_for_terminal_run_is_discarded(shell, monkeypatch, status):
    """终态后到点的重试消息必须丢弃：判据是 status，不是 next_retry_at。"""
    run = _FakeRun(status=status)
    shell(run)
    called: list[str] = []
    monkeypatch.setattr(er, "_execute", lambda *a: called.append("x"))
    assert er.execute_run(run.id) == f"discarded-{status}"
    assert called == []


def test_retrying_run_bumps_attempt_and_enters_running(shell, monkeypatch):
    """重试到点：递增 attempt、清 next_retry_at、迁移 running 后再执行。

    在消费时递增而不是调度时递增，是为了让 attempt 的语义等于「正在跑第几次」。
    """
    run = _FakeRun(status=RunStatus.RETRYING.value, attempt=1)
    run.next_retry_at = "2026-01-01T00:00:00+00:00"
    session = shell(run)
    seen: list[str] = []

    async def _fake_execute(_db, target, _agent, **_kw) -> None:
        seen.append(target.status)

    monkeypatch.setattr(er, "_execute", _fake_execute)
    logs: list[str] = []
    monkeypatch.setattr(
        er.run_events, "log_line", lambda _r, line, **_k: logs.append(line)
    )

    assert er.execute_run(run.id) == RunStatus.RUNNING.value
    assert run.bumped == 1
    assert run.attempt == 2
    assert run.next_retry_at is None
    # _execute 看到的是已迁移后的 running
    assert seen == [RunStatus.RUNNING.value]
    assert any("第 2 次尝试开始" in line for line in logs)
    assert session.commits >= 1


def test_quota_exceeded_marks_run_failed(shell, monkeypatch):
    """Worker 侧配额兜底：入队不回滚，但执行前必须拦住。"""
    run = _FakeRun(status=RunStatus.RUNNING.value)
    shell(run)

    async def _deny(_db, _owner_id) -> None:
        raise QuotaExceeded(usage_cents=12000, quota_cents=10000)

    monkeypatch.setattr(er, "assert_user_quota", _deny)
    events: list[tuple[str, str]] = []
    monkeypatch.setattr(
        er.run_events, "run_failed", lambda rid, err: events.append(("failed", err))
    )
    executed: list[str] = []
    monkeypatch.setattr(er, "_execute", lambda *a: executed.append("x"))

    assert er.execute_run(run.id) == RunStatus.FAILED.value
    assert executed == []
    assert run.status == RunStatus.FAILED.value
    assert run.error == "月配额已用尽，任务被拒绝执行"
    assert events == [("failed", run.error)]


def test_fatal_error_recycles_queued_run_through_running(shell, monkeypatch):
    """`_main` 抛不可恢复异常：仍要落 failed，且 queued 必须先经 running。

    状态机只允许 queued → running → failed；直接 queued → failed 会抛
    TransitionError，反而把「兜底」变成第二个故障。
    """
    run = _FakeRun(status=RunStatus.QUEUED.value)
    session = shell(run)

    async def _explode(*_a) -> None:
        raise RuntimeError("连接层彻底不可用")

    monkeypatch.setattr(er, "_load_context", _explode)
    events: list[str] = []
    monkeypatch.setattr(
        er.run_events, "run_failed", lambda _rid, err: events.append(err)
    )

    assert er.execute_run(run.id) == RunStatus.FAILED.value
    assert run.status == RunStatus.FAILED.value
    assert run.error is not None and "连接层彻底不可用" in run.error
    assert events == [run.error]
    assert session.commits >= 1


def test_lock_is_released_even_when_task_fails(shell, monkeypatch):
    """锁必须无条件释放：漏放会让该 Run 在 TTL 内彻底无法重跑。

    同时验证释放带上的是**本次**获取到的 token——否则 CAS 形同虚设。
    """
    run = _FakeRun(status=RunStatus.QUEUED.value)
    shell(run)

    async def _explode(*_a) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(er, "_load_context", _explode)
    monkeypatch.setattr(er.run_events, "run_failed", lambda *_a, **_k: None)

    er.execute_run(run.id)
    assert shell.released == [(run.id, "test-token")]  # type: ignore[attr-defined]


def test_successful_run_returns_final_status(shell, monkeypatch):
    """正常路径：执行后 refresh 并返回落库状态。"""
    run = _FakeRun(status=RunStatus.RUNNING.value)
    session = shell(run)

    async def _complete(_db, target, _agent, **_kw) -> None:
        target.status = RunStatus.COMPLETED.value

    monkeypatch.setattr(er, "_execute", _complete)

    assert er.execute_run(run.id) == RunStatus.COMPLETED.value
    assert session.refreshed == 1
