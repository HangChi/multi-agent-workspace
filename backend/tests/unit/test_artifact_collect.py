"""制品回收边界测试。"""

import hashlib

import pytest

from app.executors.sandbox_client import SandboxOutputLimit
from app.services import sandbox_service


def test_duplicate_names_are_suffixed() -> None:
    used: set[str] = set()
    assert sandbox_service._unique_filename("result.csv", used) == "result.csv"
    used.add("result.csv")
    assert sandbox_service._unique_filename("result.csv", used) == "result-2.csv"
    used.add("result-2.csv")
    assert sandbox_service._unique_filename("result.csv", used) == "result-3.csv"


def test_binary_mime_fallback() -> None:
    assert sandbox_service.mimetypes.guess_type("data.unknown-ext")[0] is None


@pytest.mark.parametrize("reason", ["files", "file_size", "total_size"])
def test_output_limit_is_a_structured_error(reason: str) -> None:
    error = SandboxOutputLimit(reason, "超过上限")
    assert error.reason == reason
    assert str(error) == "超过上限"


def test_sha256_is_content_based() -> None:
    assert sandbox_service._sha256_text("hello") == hashlib.sha256(b"hello").hexdigest()
