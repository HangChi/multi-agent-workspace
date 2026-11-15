"""沙箱失败分类测试。"""

from types import SimpleNamespace

import pytest

from app.executors.sandbox import classify_error, classify_outcome


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


@pytest.mark.parametrize("stderr,category", [
    (b"OSError: [Errno 28] No space left on device", "disk"),
    (b"bash: fork: retry: Resource temporarily unavailable", "pids"),
    (b"BlockingIOError: [Errno 11] Resource temporarily unavailable\n  File subprocess.py, in _fork_exec", "pids"),
])
def test_failed_scripts_report_readable_os_resource_errors(stderr, category):
    assert classify_outcome(outcome(exit_code=1, stderr=stderr)) == ("resource", category)


@pytest.mark.parametrize("stderr", [
    b"Resource temporarily unavailable while connecting to remote host",
    b"OSError: [Errno 11] Resource temporarily unavailable",
    b"my report talks about disk and pids",
])
def test_network_or_arbitrary_error_text_is_not_misclassified_as_pids(stderr):
    assert classify_outcome(outcome(exit_code=1, stderr=stderr)) == ("failed", "exit_code")


def test_handled_resource_exception_does_not_turn_success_into_failure():
    assert classify_outcome(outcome(exit_code=0, stderr=b"No space left on device")) == ("ok", None)


def test_boundary_exceptions_have_user_readable_categories() -> None:
    assert classify_error(RuntimeError("Read timed out")) == ("timeout", "timeout")
    assert classify_error(RuntimeError("ENOSPC: no space left on device")) == ("resource", "disk")
    assert classify_error(RuntimeError("pids limit reached")) == ("resource", "pids")
    assert classify_error(RuntimeError("docker unavailable")) == ("infra", "infra")
