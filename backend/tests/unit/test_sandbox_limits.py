"""沙箱失败分类测试。"""

from types import SimpleNamespace

from app.executors.sandbox import classify_outcome


def outcome(**kwargs):
    values = {"cancelled": False, "timed_out": False, "oom_killed": False, "exit_code": 0}
    values.update(kwargs)
    return SimpleNamespace(**values)


def test_success_is_ok() -> None:
    assert classify_outcome(outcome()) == ("ok", None)


def test_nonzero_exit_is_failed() -> None:
    assert classify_outcome(outcome(exit_code=2)) == ("failed", "exit_code")


def test_timeout_cancel_and_oom_are_classified() -> None:
    assert classify_outcome(outcome(timed_out=True)) == ("timeout", "timeout")
    assert classify_outcome(outcome(cancelled=True)) == ("failed", "cancelled")
    assert classify_outcome(outcome(exit_code=137, oom_killed=True)) == ("resource", "oom")


def test_resource_exit_137_fallback_is_oom() -> None:
    assert classify_outcome(outcome(exit_code=137)) == ("resource", "oom")
