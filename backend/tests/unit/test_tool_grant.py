"""Agent 工具授权注入测试。"""

from app.executors.sandbox import build_executors


def test_only_granted_tools_are_registered() -> None:
    registry = build_executors(["calculate"])
    assert set(registry) == {"calculate"}


def test_empty_grant_has_no_tools() -> None:
    assert build_executors([]) == {}


def test_unknown_or_retired_grant_is_skipped() -> None:
    registry = build_executors(["web_fetch", "http", "not-a-tool"])
    assert registry == {}
