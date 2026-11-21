"""错误文案单一来源 + 归类不依赖字符串（R2，docs/code-audit-2026-11-21.md）。

两类风险，行为上都不报错，所以既有的等值断言全抓不到：

1. **契约要求文案逐字一致**。016 契约 §2：非相关人的 404 message 必须与
   「资源确实不存在」逐字相同，否则 message 自身泄露存在性。同一句话写在
   十几个地方，任何一处被改成「找不到项目」就是契约破裂。

2. **有人拿文案当控制流**。`schedule_service._translate_assign_failure`
   原先靠 `"归档" in message` 归类。改一个字，归类就静默落到兜底
   「目标不可用」。

第 2 条是本文件的重点：`test_rewording_does_not_break_classification`
直接**把文案改掉**再断言归类不变——这是唯一能证明「不再依赖字符串」的方式。
"""

from __future__ import annotations

import pathlib
import re

import pytest

from app.api.errors import AppError
from app.api.messages import (
    DEPENDENCY_TARGET_MESSAGE,
    ERROR_MESSAGES,
    FailureKind,
    failure_kind_of,
    mark_failure,
    raise_for_missing,
)
from app.services.schedule_service import _translate_assign_failure

# ---------- 文案表本身 ----------


def test_every_message_is_non_empty_and_unambiguous():
    """表里的文案必须非空，且同一个文案不映射到两个资源。

    两条资源共用一个文案会让「401 那个不存在」这类排查失去意义；
    空文案则会让前端只能显示一个空白的 toast。
    """
    for key, message in ERROR_MESSAGES.items():
        assert message, f"{key} 的文案为空"
        assert message.endswith("不存在"), f"{key} 的文案不像 404：{message}"

    values = list(ERROR_MESSAGES.values())
    assert len(values) == len(set(values)), "存在两个资源共用同一句 404 文案"


def test_missing_resource_key_raises_instead_of_guessing():
    """拼错的资源名必须立刻炸，不能静默生成「资源不存在」。

    给一个兜底文案是最容易写的实现，也正是文案漂移的起点：
    拼错了照样能用，于是没人发现它不对。
    """
    with pytest.raises(KeyError):
        raise_for_missing("nonexistent_resource")


def test_raise_for_missing_carries_structured_marker():
    with pytest.raises(AppError) as exc:
        raise_for_missing("project")
    assert exc.value.code == "NOT_FOUND"
    assert exc.value.message == ERROR_MESSAGES["project"]
    assert failure_kind_of(exc.value) is FailureKind.MISSING


# ---------- 归类不再依赖字符串（核心） ----------


def test_archived_marker_beats_message_text():
    """标记优先于文案：这两项判断完全不读 message。"""
    exc = mark_failure(
        AppError("INVALID_INPUT", "归档 Agent 不可分配"), FailureKind.ARCHIVED
    )
    assert _translate_assign_failure(exc) == "目标已归档"

    missing = mark_failure(AppError("NOT_FOUND", "Agent 不存在"), FailureKind.MISSING)
    assert _translate_assign_failure(missing) == "目标已删除"


def test_rewording_does_not_break_classification():
    """把文案改成完全不含关键词的句子，归类仍然正确。

    这是 R2 的核心断言。原实现读 `"归档" in message`，
    把「归档 Agent 不可分配」改成「该 Agent 已停用」后就会掉进兜底。
    现在判据是结构化标记，文案可以随便改。

    反向验证：把 `_translate_assign_failure` 的结构化分支删掉（只留关键词
    兜底），本用例立刻变红——两个断言都会落到「目标不可用」。
    """
    paraphrases = [
        (
            "INVALID_INPUT",
            "该 Agent 已停用，无法调度",
            FailureKind.ARCHIVED,
            "目标已归档",
        ),
        ("NOT_FOUND", "找不到这个执行目标", FailureKind.MISSING, "目标已删除"),
        ("RATE_LIMITED", "本计费周期额度耗尽", FailureKind.QUOTA, "本月配额已用尽"),
    ]
    for code, text, kind, expected in paraphrases:
        exc = mark_failure(AppError(code, text), kind)
        assert _translate_assign_failure(exc) == expected, (
            f"改文案后归类错了：{text!r} → {_translate_assign_failure(exc)}"
        )


def test_unmarked_exception_falls_back_to_keyword_scan():
    """未打标记的历史异常仍按旧规则归类（不改变既有行为）。

    保留兜底是刻意的：库里还有别的模块抛没打标记的 AppError，
    直接删掉兜底会让它们的归类从「目标已归档」变成「目标不可用」，
    那是行为变更而非重构。
    """
    assert (
        _translate_assign_failure(AppError("INVALID_INPUT", "归档 Agent 不可分配"))
        == "目标已归档"
    )
    assert (
        _translate_assign_failure(AppError("NOT_FOUND", "Agent 不存在")) == "目标已删除"
    )
    assert (
        _translate_assign_failure(AppError("CONFLICT", "别的什么原因")) == "目标不可用"
    )


def test_quota_exceeded_is_recognized_without_message():
    """`QuotaExceeded` 靠 code 就能认出来，不依赖文案。"""

    class FakeQuota(AppError):
        def __init__(self) -> None:
            super().__init__("RATE_LIMITED", "额度耗尽了")

    assert _translate_assign_failure(FakeQuota()) == "本月配额已用尽"


# ---------- 依赖目标文案与主文案刻意不同 ----------


def test_dependency_target_message_differs_from_request_message():
    """「前置需求不存在」必须与「需求不存在」不同。

    若两者相同，「我加的边指向一个不存在的需求」会和「我没权限看这个需求」
    变得无法区分——前端提示会指错方向。
    """
    assert DEPENDENCY_TARGET_MESSAGE != ERROR_MESSAGES["request"]
    assert DEPENDENCY_TARGET_MESSAGE not in ERROR_MESSAGES.values()


# ---------- 生产代码不再写裸字面量 ----------


def test_no_module_raises_a_bare_404_literal_for_mapped_resources():
    """已经收进表的文案，生产代码里不该再出现裸字面量。

    只查「表和调用点是否一致」，不查全部 404——有些 404 是端点专属措辞
    （如「市场 listing 不可见」），它们本来就不该进通用表。

    反向验证：把任一 `raise_for_missing("project")` 改回
    `raise AppError("NOT_FOUND", "项目不存在")`，本用例变红。
    """
    app_dir = pathlib.Path(__file__).resolve().parents[2] / "app"
    offenders: list[str] = []
    for path in app_dir.rglob("*.py"):
        if path.name == "messages.py":
            continue
        text = path.read_text(encoding="utf-8")
        for message in ERROR_MESSAGES.values():
            # 允许 messages.py 自己定义；其余文件出现即视为漏改
            if re.search(
                rf'AppError\(\s*"NOT_FOUND"\s*,\s*"{re.escape(message)}"', text
            ):
                offenders.append(f"{path.relative_to(app_dir)}: {message}")
    assert not offenders, "仍有裸 404 字面量：\n" + "\n".join(offenders)
