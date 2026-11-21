"""状态集合的单一来源守护（R3，docs/code-audit-2026-11-21.md）。

这类测试不测行为，测的是**「下一次加状态时会不会又漏」**。

背景：`retrying` 是 006 引入的 Run 状态。加它的时候没人去改
`request_service._ACTIVE_RUN_STATUSES`、`reaper_service._ACTIVE_STATUSES`、
`stats_service._NON_TERMINAL` 这三份手写字面量，于是三处同时漂移，
症状各不相同（需求提前收敛 / 需求被误标 failed / 仪表盘少算），
而且**没有任何一个既有测试变红**——因为三处都是「漏掉一类值」，
不是「算错一个值」。

现在集合全部从状态机派生。这里加两道锁：
1. 派生关系本身正确（终态+非终态 = 全集，且不相交）；
2. 各模块**不再**持有字面量副本（用 AST 检查，防止有人图省事又写回去）。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.orchestrator import request_state_machine, state_machine
from app.orchestrator.request_state_machine import RequestStatus
from app.orchestrator.state_machine import RunStatus

BACKEND_APP = Path(__file__).resolve().parents[2] / "app"


# ---------- 派生关系 ----------


def test_run_status_sets_partition_the_enum():
    """终态 ∪ 非终态 = 全部 Run 状态，且两者不相交。"""
    all_values = {s.value for s in RunStatus}
    terminal = state_machine.TERMINAL_RUN_STATUSES
    active = state_machine.ACTIVE_RUN_STATUSES

    assert terminal | active == all_values
    assert terminal & active == frozenset()


def test_request_status_sets_partition_the_enum():
    all_values = {s.value for s in RequestStatus}
    terminal = request_state_machine.TERMINAL_STATUSES
    open_ = request_state_machine.OPEN_STATUSES

    assert terminal | open_ == all_values
    assert terminal & open_ == frozenset()


def test_new_run_status_is_active_by_default():
    """新增状态默认算「还没结束」——这是安全侧。

    反过来（默认算终态）会让一个刚加的执行中状态被 settle 当成已完成，
    比「多等一会儿」危险得多。`retrying` 就是靠这条规则自动纳入的。
    """
    assert state_machine.ACTIVE_RUN_STATUSES == (
        frozenset(s.value for s in RunStatus) - state_machine.TERMINAL_RUN_STATUSES
    )
    # 具体到 006 的状态，明确钉死（防有人把它从 ACTIVE 里手动剔除）
    assert RunStatus.RETRYING.value in state_machine.ACTIVE_RUN_STATUSES
    assert RunStatus.RETRYING.value not in state_machine.TERMINAL_RUN_STATUSES


def test_request_terminal_excludes_run_only_statuses():
    """需求终态集合里不得出现 Run 专有值（曾混入 `timeout`）。"""
    for status in request_state_machine.TERMINAL_STATUSES:
        # 只要不是合法 RequestStatus 就会抛 ValueError
        assert RequestStatus(status)
    assert "timeout" not in request_state_machine.TERMINAL_STATUSES


def test_success_and_failure_sets_are_disjoint_and_cover_terminal():
    """成功 ∪ 失败 ⊆ Run 终态；`cancelled` 两边都不算（用户主动放弃）。"""
    success = state_machine.SUCCESS_RUN_STATUSES
    failed = state_machine.FAILED_RUN_STATUSES
    terminal = state_machine.TERMINAL_RUN_STATUSES

    assert success & failed == frozenset()
    assert success | failed <= terminal
    assert RunStatus.CANCELLED.value not in success
    assert RunStatus.CANCELLED.value not in failed


# ---------- 各模块不得再写字面量副本 ----------


def _module_status_literals(rel_path: str) -> list[set[str]]:
    """扫出模块里「像状态集合」的字面量集合/元组。

    判据：由字符串常量构成的 set/tuple（至少 2 个元素），且元素都像状态词
    （全小写、只含字母和下划线）。这样 `{"", ...}` 之类不会误报。
    """
    path = BACKEND_APP / rel_path
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[set[str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Set, ast.Tuple, ast.List)):
            continue
        values = {
            elt.value
            for elt in node.elts
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
        }
        if len(values) < 2 or len(values) != len(node.elts):
            continue
        if all(v.replace("_", "").isalpha() and v.islower() for v in values):
            found.append(values)
    return found


_KNOWN_STATUS_WORDS = {
    "created",
    "pending",
    "accepted",
    "assigned",
    "running",
    "completed",
    "failed",
    "cancelled",
    "queued",
    "needs_input",
    "retrying",
    "timeout",
}


@pytest.mark.parametrize(
    "module",
    [
        "services/request_service.py",
        "services/reaper_service.py",
        "services/stats_service.py",
        "services/dependency_service.py",
        "services/dependency_graph_service.py",
        "services/schedule_service.py",
        "orchestrator/state_machine.py",
        "orchestrator/request_state_machine.py",
    ],
)
def test_no_module_redeclares_a_status_set(module: str):
    """这些模块里的状态集合必须来自状态机，不得再写字面量副本。

    反向验证：在任一模块里加回 `_X = frozenset({"queued", "running"})`，本用例变红。
    """
    offenders = [
        values
        for values in _module_status_literals(module)
        if values & _KNOWN_STATUS_WORDS
    ]
    assert not offenders, f"{module} 仍有状态字面量副本：{offenders}"
