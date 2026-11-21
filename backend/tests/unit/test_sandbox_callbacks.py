"""沙箱生命周期回调（R4 从 `_execute` 内嵌闭包搬出）。

搬迁前这两个回调**没有任何直接测试**——`test_runner.py` 只是往 runner 上挂
了假回调，验证的是 runner 会不会调它，而不是回调本身做了什么。所以这次
搬完按新参数形态把它们逐段钉住，重点在两条容易改坏的边界：

1. `persist_sandbox_result` 是**步骤级**收尾：沙箱失败只把这一步标 failed，
   **不**终结 Run。有人看到「外部工具失败直接 run_failed」就想对齐，那会让
   一次沙箱报错连带整个 Run 死掉。
2. `publish_sandbox_started` 必须**早于**真实执行把 step 行和 execution 行
   写进去：前端拿 execution_id 去订阅输出流，晚一步就丢开头那段输出。
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.tasks import sandbox_callbacks as cb


def _run() -> SimpleNamespace:
    return SimpleNamespace(id="run-1", owner_id="user-1")


def _agent() -> SimpleNamespace:
    return SimpleNamespace(id="agent-1")


def _db(persisted_step: SimpleNamespace | None = None) -> SimpleNamespace:
    db = SimpleNamespace(
        get=AsyncMock(return_value=persisted_step),
        commit=AsyncMock(),
        flush=AsyncMock(),
        add=Mock(),
    )
    return db


@pytest.fixture
def services(monkeypatch):
    """打桩 sandbox_service / egress_service / run_events。"""
    persist = AsyncMock(
        return_value=SimpleNamespace(
            id="exec-1", status="completed", exit_code=0, duration_ms=12
        )
    )
    record_accesses = AsyncMock(return_value=[])
    start_execution = AsyncMock()

    finished = Mock()
    started = Mock()
    denied = Mock()
    output = Mock()
    for name, fn in (
        ("sandbox_finished", finished),
        ("sandbox_started", started),
        ("egress_denied", denied),
        ("sandbox_output", output),
    ):
        monkeypatch.setattr(cb.run_events, name, fn)

    monkeypatch.setattr(cb.sandbox_service, "persist_sandbox_result", persist)
    monkeypatch.setattr(cb.sandbox_service, "start_execution", start_execution)
    monkeypatch.setattr(cb.egress_service, "record_accesses", record_accesses)
    monkeypatch.setattr(
        cb.egress_service,
        "snapshot_for_execution",
        AsyncMock(return_value=("allow", [], [])),
    )
    monkeypatch.setattr(cb, "write_audit", AsyncMock())

    return SimpleNamespace(
        persist=persist,
        record_accesses=record_accesses,
        start_execution=start_execution,
        finished=finished,
        started=started,
        denied=denied,
        sandbox_output=output,
    )


def _result_detail(ok: bool = True) -> dict:
    return {
        "step_id": "step-1",
        "step": {"output": {"ok": ok}, "duration_ms": 12},
        "payload": {"artifacts": [{"name": "a.csv"}], "egress_accesses": []},
        "result": {"ok": ok},
    }


async def test_failed_sandbox_marks_step_failed_but_does_not_touch_the_run(
    services,
) -> None:
    """沙箱失败 ≠ Run 失败：只把这一步标 failed，Run 由 runner 继续推进。

    这条是「对齐外部工具失败语义」想法的反面约束——那个 `_settle(FAILED)`
    属于 Run 级，这里是步骤级，对齐会让一次沙箱报错连带整个 Run 死掉。
    """
    persisted = SimpleNamespace()
    db = _db(persisted)

    await cb.persist_sandbox_result(db, _run(), _agent(), _result_detail(ok=False))

    assert persisted.status == "failed"
    assert persisted.output == {"ok": False}
    services.finished.assert_called_once()


async def test_successful_sandbox_marks_step_completed(services) -> None:
    persisted = SimpleNamespace()
    db = _db(persisted)

    await cb.persist_sandbox_result(db, _run(), _agent(), _result_detail(ok=True))

    assert persisted.status == "completed"


async def test_persist_requires_agent_ownership(services) -> None:
    """没有 Agent 就没有归属，宁可炸也不能写出一条 owner 为空的事实。"""
    with pytest.raises(RuntimeError, match="缺少 Agent 归属"):
        await cb.persist_sandbox_result(_db(), _run(), None, _result_detail())


async def test_persist_rejects_uninitialized_step(services) -> None:
    """step 行必须已由 `publish_sandbox_started` 落好；查不到说明顺序错了。"""
    with pytest.raises(RuntimeError, match="未初始化"):
        await cb.persist_sandbox_result(_db(None), _run(), _agent(), _result_detail())


async def test_finished_event_carries_artifact_count_not_the_list(services) -> None:
    """事件里只带数量：制品清单可能很长，塞进事件会被逐条复制到每个订阅者。"""
    db = _db(SimpleNamespace())

    await cb.persist_sandbox_result(db, _run(), _agent(), _result_detail())

    assert services.finished.call_args.kwargs["artifacts"] == 1


@pytest.mark.parametrize("action", ["allow", "deny"])
async def test_only_denied_egress_is_audited(services, action) -> None:
    """放行不写审计，只有 deny 才写——否则审计表会被正常访问淹没。"""
    services.record_accesses.return_value = [
        SimpleNamespace(
            action=action,
            target_ip="10.0.0.1",
            target_domain=None,
            port=443,
            matched_rule_id="rule-1",
        )
    ]
    db = _db(SimpleNamespace())

    await cb.persist_sandbox_result(db, _run(), _agent(), _result_detail())

    if action == "deny":
        cb.write_audit.assert_awaited_once()
        assert services.denied.call_count == 1
        assert services.denied.call_args.kwargs["target_domain"] == "ip-direct"
    else:
        cb.write_audit.assert_not_awaited()
        services.denied.assert_not_called()


async def test_started_writes_step_and_execution_before_emitting(services) -> None:
    """执行行必须先落库再发事件：前端拿 execution_id 订阅输出流。"""
    db = _db()
    calls: list[str] = []
    db.commit.side_effect = lambda: calls.append("commit")
    services.started.side_effect = lambda *a, **k: calls.append("event")

    await cb.publish_sandbox_started(
        db,
        _run(),
        _agent(),
        {},
        {
            "tool_name": "python_exec",
            "execution_id": "exec-1",
            "step_seq": 3,
            "step_id": "step-1",
            "language": "python",
            "limits": {"timeout": 10},
            "code": "print(1)",
        },
    )

    db.add.assert_called_once()
    services.start_execution.assert_awaited_once()
    assert calls == ["commit", "event"]


async def test_egress_snapshot_is_taken_before_execution_starts(services) -> None:
    """规则快照必须在执行前取；执行中途改规则不能影响正在跑的这次。"""
    executor = SimpleNamespace(set_egress_snapshot=Mock())
    db = _db()

    await cb.publish_sandbox_started(
        db,
        _run(),
        _agent(),
        {"python_exec": executor},
        {
            "tool_name": "python_exec",
            "execution_id": "exec-1",
            "step_seq": 1,
            "step_id": "step-1",
            "language": "python",
            "limits": {},
            "code": "",
        },
    )

    executor.set_egress_snapshot.assert_called_once()
    services.start_execution.assert_awaited_once()


async def test_output_callback_is_wired_with_the_execution_id(services) -> None:
    """输出回调要绑到本次 execution：绑错会让 A 的输出串到 B 的日志里。

    回调本身是同步的（沙箱读取线程直接调），所以这里同步调用来断言参数。
    """
    set_output_callback = Mock()
    executor = SimpleNamespace(set_output_callback=set_output_callback)

    await cb.publish_sandbox_started(
        _db(),
        _run(),
        _agent(),
        {"python_exec": executor},
        {
            "tool_name": "python_exec",
            "execution_id": "exec-9",
            "step_seq": 7,
            "step_id": "step-1",
            "language": "python",
            "limits": {},
            "code": "",
        },
    )

    publish_output = set_output_callback.call_args.args[0]
    publish_output("stdout", "hello")

    services.sandbox_output.assert_called_once_with(
        "run-1", "exec-9", "stdout", "hello"
    )


async def test_executor_without_callback_hooks_does_not_crash(services) -> None:
    """不是所有执行器都实现这两个可选钩子（比如纯本地工具）。"""
    await cb.publish_sandbox_started(
        _db(),
        _run(),
        _agent(),
        {"plain_tool": SimpleNamespace()},
        {
            "tool_name": "plain_tool",
            "execution_id": "exec-1",
            "step_seq": 1,
            "step_id": "step-1",
            "language": "python",
            "limits": {},
            "code": "",
        },
    )

    services.start_execution.assert_awaited_once()
