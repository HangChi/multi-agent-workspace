"""Celery 任务壳的单测（015）：钉住「失败要让 Beat 看见」这类策略。

任务壳本身很薄（起 session → 调服务 → 记日志），但它承载了三条容易改坏的
策略，而这三条都不会在集成测试里被顺带覆盖到：

1. 扫描任务**必须重抛**异常。吞掉的话 Beat 认为这一分钟跑过了，Celery 的
   失败计数也看不到——「调度悄悄停摆」是最难发现的一类故障。
2. 回收任务里**事件推送失败不能影响回收结果**：Run 已经被标记 failed（事实），
   推不动只是通知丢了，不能让整个任务失败回滚。
3. 每个任务都要用自己的 session 并自行收尾（commit 由服务层/任务层负责）。

用 fake session 而不是真库：这些测试关心的是「谁被调用、异常怎么走」，
接真库只会让它们变慢变脆。真正的 SQL 语义由 integration 里
test_sandbox_retention / test_schedule_history 覆盖。
"""

import uuid

import pytest

from app.tasks import maintenance, schedule, validate_external_server


class _FakeSession:
    """最小 async session：只提供任务壳用到的方法。"""

    def __init__(self) -> None:
        self.committed = 0
        self.scalar_result = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info) -> bool:
        return False

    async def commit(self) -> None:
        self.committed += 1

    async def scalar(self, _stmt):  # noqa: ANN001 - 测试替身
        return self.scalar_result


@pytest.fixture
def fake_session(monkeypatch) -> _FakeSession:
    session = _FakeSession()

    def _factory() -> _FakeSession:
        return session

    monkeypatch.setattr(schedule, "SessionLocal", _factory)
    monkeypatch.setattr(maintenance, "SessionLocal", _factory)
    monkeypatch.setattr(validate_external_server, "SessionLocal", _factory)
    return session


# ---------- 调度扫描 ----------


def test_scan_task_returns_processed_count(fake_session, monkeypatch):
    async def _fake_run(_db) -> int:  # noqa: ANN001
        return 3

    monkeypatch.setattr(schedule, "run_due_schedules", _fake_run)
    assert schedule.scan_due_schedules_task() == 3


def test_scan_task_reraises_so_beat_sees_failure(fake_session, monkeypatch):
    """服务层抛错必须冒到 Celery：吞掉会让「扫描停摆」无声无息。"""

    async def _boom(_db) -> int:  # noqa: ANN001
        raise RuntimeError("db down")

    monkeypatch.setattr(schedule, "run_due_schedules", _boom)
    with pytest.raises(RuntimeError):
        schedule.scan_due_schedules_task()


# ---------- 僵尸 Run 回收 ----------


def test_reap_task_returns_ids_and_commits(fake_session, monkeypatch):
    async def _fake_reap(_db) -> list[str]:  # noqa: ANN001
        return ["run-1"]

    monkeypatch.setattr(maintenance, "reap_stale_runs", _fake_reap)
    monkeypatch.setattr(maintenance.run_events, "run_failed", lambda *a, **k: None)

    assert maintenance.reap_stale_runs_task() == ["run-1"]
    assert fake_session.committed == 1


def test_reap_task_survives_event_push_failure(fake_session, monkeypatch):
    """推送失败不能把回收结果一起吞掉：Run 已经被标记 failed，这个事实要留住。"""

    async def _fake_reap(_db) -> list[str]:  # noqa: ANN001
        return ["run-2"]

    def _explode(*_args, **_kwargs):
        raise RuntimeError("redis down")

    monkeypatch.setattr(maintenance, "reap_stale_runs", _fake_reap)
    monkeypatch.setattr(maintenance.run_events, "run_failed", _explode)

    assert maintenance.reap_stale_runs_task() == ["run-2"]


# ---------- 数据清理 ----------


def test_purge_task_returns_purged_count(fake_session, monkeypatch):
    async def _fake_purge(_db) -> int:  # noqa: ANN001
        return 7

    monkeypatch.setattr(maintenance, "purge_expired_sandbox_data", _fake_purge)
    assert maintenance.purge_expired_sandbox_data_task() == 7
    assert fake_session.committed == 1


def test_prune_history_task_returns_removed_count(fake_session, monkeypatch):
    async def _fake_prune(_db) -> int:  # noqa: ANN001
        return 12

    monkeypatch.setattr(maintenance, "prune_schedule_history", _fake_prune)
    assert maintenance.prune_schedule_history_task() == 12


# ---------- 外部服务校验 ----------


def test_validate_task_returns_not_found_for_missing_server(fake_session):
    fake_session.scalar_result = None
    assert (
        validate_external_server.validate_external_server(str(uuid.uuid4()), "u-1")
        == "not_found"
    )
