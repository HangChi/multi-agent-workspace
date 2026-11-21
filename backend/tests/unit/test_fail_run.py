"""`_fail_run` 的两个参数各自代表一处真实差异（R4）。

原先 `execute_run.py` 里「标失败 + 落库 + 发事件」这段出现 6 次，逐字相同的
只有中间两行。抽取时把差异做成了参数，本文件就是**把差异钉住**：

1. `settle`：要不要顺手收敛需求状态。
   - `finally` 里统一收敛的那条路径 → `settle=False`（否则会做两次）；
   - 早失败（需求行不存在、组内无成员）→ `settle=False`（需求本身就是问题）；
   - 配额兜底、致命错误 → `settle=True`（需求已分配出去，必须收口）。
2. `emit_to`：事件用哪个 id。
   闭包里 `run` 是另一个 session 重新读出来的对象，那里用闭包捕获的 `run_id`
   更直接。两者语义相同，但改掉任何一个都属于「为了整齐而动行为」。

用 fake session / 打桩 `run_events` 与 `settle_request_status`：
这里要验的是「调了谁、按什么顺序、带什么参数」，不是 SQL 语义。
真 SQL 语义由 integration 的 execute_run 用例覆盖。
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.orchestrator.state_machine import RunStatus
from app.tasks import execute_run as module


def _run(status: str = "running") -> SimpleNamespace:
    """最小 Run 替身。

    `mark_finished` / `mark_started` 不是可有可无的装饰：状态机在迁移到终态时
    会调用它们打时间戳。少了会报 AttributeError，看着像被测函数的问题，
    其实是替身不完整。
    """
    return SimpleNamespace(
        id="run-abc123",
        status=status,
        error=None,
        request_id="req-1",
        started_at=None,
        finished_at=None,
        mark_started=lambda: None,
        mark_finished=lambda: None,
    )


def _db() -> SimpleNamespace:
    return SimpleNamespace(commit=AsyncMock())


@pytest.fixture
def patched(monkeypatch):
    """打桩事件推送与需求收敛，返回可断言的 mock。"""
    failed = Mock()
    monkeypatch.setattr(module.run_events, "run_failed", failed)

    settle = AsyncMock(return_value=False)
    target = "app.tasks.execute_run.settle_request_status"
    monkeypatch.setattr(target, settle)
    return SimpleNamespace(run_failed=failed, settle=settle)


async def test_fail_run_sets_status_commits_and_emits(patched) -> None:
    run, db = _run(), _db()
    await module._fail_run(db, run, "boom", settle=False)

    assert run.error == "boom"
    assert run.status == RunStatus.FAILED.value
    db.commit.assert_awaited_once()
    patched.run_failed.assert_called_once_with(run.id, "boom")


async def test_error_is_written_before_event_is_emitted(patched) -> None:
    """事件里的文案必须就是刚写进 run 的那份，不能是上一次的残留。"""
    run = _run()
    run.error = "上一次的错误"

    captured: list[str | None] = []
    patched.run_failed.side_effect = lambda _id, err: captured.append(err)

    await module._fail_run(_db(), run, "这次的原因", settle=False)

    assert captured == ["这次的原因"]
    assert run.error == "这次的原因"


async def test_settle_false_skips_request_settlement(patched) -> None:
    """`finally` 里会统一收敛的路径不能在这里抢先做，否则同一事务收敛两次。"""
    await module._fail_run(_db(), _run(), "boom", settle=False)
    patched.settle.assert_not_awaited()


async def test_settle_true_settles_and_commits_when_it_changed_something(
    patched,
) -> None:
    patched.settle.return_value = True
    db = _db()

    await module._fail_run(db, _run(), "boom", settle=True)

    patched.settle.assert_awaited_once()
    # 两次 commit：标失败一次、需求收敛一次（收敛确实改了行才提交）
    assert db.commit.await_count == 2


async def test_settle_true_does_not_commit_when_nothing_changed(patched) -> None:
    """收敛返回 False 表示没有行被改动；此时多一次 commit 是空事务。"""
    patched.settle.return_value = False
    db = _db()

    await module._fail_run(db, _run(), "boom", settle=True)

    db.commit.assert_awaited_once()


async def test_emit_to_overrides_event_id_but_not_the_row(patched) -> None:
    """`emit_to` 只影响事件里的 id，落库对象仍是传进来的那个 run。"""
    run = _run()

    await module._fail_run(_db(), run, "boom", settle=False, emit_to="run-from-closure")

    patched.run_failed.assert_called_once_with("run-from-closure", "boom")
    assert run.status == RunStatus.FAILED.value


async def test_fail_run_requires_running_state() -> None:
    """状态机约束不能因为抽了函数就被绕过：queued 直接 fail 必须抛。

    `execute_run` 的致命错误分支因此先显式迁移到 running 再调本函数——
    那条 `if run.status == "queued": transition(...RUNNING)` 不是冗余代码。
    """
    with pytest.raises(Exception, match="queued|transition|非法|不允许"):
        await module._fail_run(_db(), _run(status="queued"), "boom", settle=False)
