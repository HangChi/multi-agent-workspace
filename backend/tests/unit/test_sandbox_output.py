"""沙箱输出边界测试。"""

from app.executors.sandbox import decode_output
from app.executors.sandbox_client import truncate_output


def test_output_keeps_tail_and_original_size() -> None:
    data, truncated, full = truncate_output(
        b"head" + b"x" * (3 * 1024 * 1024) + b"tail", 1024 * 1024
    )
    assert truncated is True
    assert len(data) == 1024 * 1024
    assert data.endswith(b"tail")
    assert full == 3 * 1024 * 1024 + 8


def test_invalid_utf8_is_replaced() -> None:
    assert decode_output(b"ok\xff\xfe") == "ok��"
