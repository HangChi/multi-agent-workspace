"""上下文裁剪：按预算省略较早的工具输出，且不产生非法消息序列。

回归点（2026-11-17 审计）：runner 的 messages 只追加不裁剪。单条 MCP 结果上限
64 KiB、沙箱输出上限 1 MB（进 step 前已脱敏，但工具结果仍会入 messages），
10 步就能堆出几十万 token —— 每一步都在为前面所有工具输出重复付费。
"""

from __future__ import annotations

from app.orchestrator.runner import CONTEXT_OMITTED_NOTE, trim_messages


def _round(index: int, payload_chars: int) -> list[dict]:
    """造一轮：assistant(tool_calls) → tool 结果。"""
    return [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": f"c{index}", "name": "read_url", "arguments": {"u": "x"}}
            ],
        },
        {"role": "tool", "name": "read_url", "content": "x" * payload_chars},
    ]


def _conversation(rounds: int, payload_chars: int) -> list[dict]:
    messages: list[dict] = [
        {"role": "system", "content": "SYSTEM"},
        {"role": "user", "content": "原始需求"},
    ]
    for index in range(rounds):
        messages.extend(_round(index, payload_chars))
    return messages


def test_no_trim_under_budget() -> None:
    messages = _conversation(2, 10)

    trimmed, omitted = trim_messages(messages, 10_000)

    assert omitted == 0
    assert trimmed is messages


def test_trim_keeps_system_and_original_request() -> None:
    messages = _conversation(10, 1_000)

    trimmed, omitted = trim_messages(messages, 3_000)

    assert omitted > 0
    assert trimmed[0] == {"role": "system", "content": "SYSTEM"}
    assert trimmed[1] == {"role": "user", "content": "原始需求"}
    assert trimmed[2]["content"] == CONTEXT_OMITTED_NOTE.format(count=omitted)


def test_trim_starts_at_assistant_boundary() -> None:
    """tool 结果必须紧跟带 tool_calls 的 assistant，不能从中间切。"""
    messages = _conversation(10, 1_000)

    trimmed, _ = trim_messages(messages, 3_000)

    assert trimmed[3]["role"] == "assistant"
    assert "tool_calls" in trimmed[3]


def test_tail_is_preserved_verbatim() -> None:
    messages = _conversation(10, 1_000)
    last = messages[-1]

    trimmed, _ = trim_messages(messages, 3_000)

    assert trimmed[-1] is last


def test_no_safe_boundary_means_no_trim() -> None:
    """只有连续 tool 消息、找不到 assistant 起点时，宁可超预算也不切碎对话。"""
    messages: list[dict] = [
        {"role": "system", "content": "SYSTEM"},
        {"role": "user", "content": "原始需求"},
        *[
            {"role": "tool", "name": "read_url", "content": "x" * 5_000}
            for _ in range(5)
        ],
    ]

    trimmed, omitted = trim_messages(messages, 1_000)

    assert omitted == 0
    assert trimmed is messages


def test_zero_budget_disables_trimming() -> None:
    messages = _conversation(10, 1_000)

    trimmed, omitted = trim_messages(messages, 0)

    assert omitted == 0
    assert trimmed is messages


def test_trim_respects_budget_for_tail() -> None:
    messages = _conversation(10, 1_000)

    trimmed, _ = trim_messages(messages, 2_500)

    tail_chars = sum(len(m.get("content") or "") for m in trimmed[2:])
    # 省略提示本身占几十字符，给 200 字符余量
    assert tail_chars <= 2_500 + 200
