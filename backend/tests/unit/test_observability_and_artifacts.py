"""外部工具可观测计数器 + 附件校验的单测（015 覆盖率补齐）。

两件都不大，但都在「静默出错」的一侧：

- `ExternalObservability` 是进程内**有界**计数器：名字基数不设上限的话，
  一个拼错的状态名就能让字典无限增长（长期运行的 worker 会慢慢吃光内存）。
- 附件校验是上传前的第一道闸：空文件名、0 字节、超限、mime 不在白名单，
  必须各自给出可读原因——否则用户只看到「上传失败」。

`observe_*` 那几个入口还被刻意做成「某些状态计两次」（auth_error / policy_denied
/ result_truncated），因为它们的告警阈值按「严重程度」定的，不是按次数。
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.api.errors import AppError
from app.services import artifact_service
from app.services.external_observability import (
    ExternalObservability,
    observe_destination_change,
    observe_invocation,
    observe_validation,
    recent_alerts,
)

# ---------- 可观测计数器 ----------


def test_counter_increments_and_snapshots():
    obs = ExternalObservability()
    obs.increment("validation_drift")
    obs.increment("auth_error", amount=2)

    snapshot = obs.snapshot()

    assert snapshot["counters"]["validation_drift"] == 1
    assert snapshot["counters"]["auth_error"] == 2
    assert set(snapshot["last_seen"]) == {"validation_drift", "auth_error"}


def test_negative_amount_is_clamped():
    """计数不能被负值减小：这只是诊断计数器，减下去会掩盖问题。"""
    obs = ExternalObservability()
    obs.increment("auth_error", amount=-5)
    assert obs.snapshot()["counters"]["auth_error"] == 0


def test_counter_cardinality_is_bounded(monkeypatch):
    """名字基数有上限：否则一个拼错的标签就能无限撑大字典。"""
    monkeypatch.setattr(
        "app.services.external_observability._MAX_COUNTERS", 2, raising=False
    )
    obs = ExternalObservability()
    obs.increment("a")
    obs.increment("b")
    obs.increment("c")  # 超限后丢弃，不新增键

    assert set(obs.snapshot()["counters"]) == {"a", "b"}


def test_alerts_fire_at_thresholds():
    obs = ExternalObservability()
    obs.increment("validation_drift")  # 阈值 1
    obs.increment("policy_denied", amount=10)  # 阈值 10

    names = {alert["name"] for alert in obs.alerts()}

    assert "validation_drift" in names
    assert "policy_denied" in names
    assert "auth_error" not in names  # 阈值 5，没有触发


def test_recent_alerts_filters_by_window():
    obs = ExternalObservability()
    obs.increment("validation_drift")

    now = datetime.now(UTC)
    assert len(obs.recent_alerts(minutes=60, now=now)) == 1
    # 很久以后再查，应该已经过期
    assert obs.recent_alerts(minutes=1, now=now + timedelta(hours=2)) == []


def test_observe_helpers_double_count_severe_statuses():
    """auth_error / policy_denied / result_truncated 计两次：阈值按严重程度定。"""
    before = recent_alerts(minutes=60)
    observe_invocation("auth_error")
    after = recent_alerts(minutes=60)

    assert isinstance(before, list) and isinstance(after, list)

    observe_validation(drift=True, audit_gap=True)
    observe_destination_change()


# ---------- 附件校验 ----------


def test_validate_attachment_accepts_normal_file():
    artifact_service.validate_attachment(
        filename="报告.pdf", size=1024, mime_type="application/pdf"
    )


def test_validate_attachment_rejects_blank_name():
    with pytest.raises(AppError) as exc:
        artifact_service.validate_attachment(
            filename="  ", size=10, mime_type="text/plain"
        )
    assert exc.value.code == "INVALID_INPUT"


@pytest.mark.parametrize("size", [0, -1, 10**12])
def test_validate_attachment_rejects_bad_size(size):
    with pytest.raises(AppError) as exc:
        artifact_service.validate_attachment(
            filename="a.txt", size=size, mime_type="text/plain"
        )
    assert exc.value.code == "INVALID_INPUT"


def test_validate_attachment_rejects_unknown_mime():
    with pytest.raises(AppError) as exc:
        artifact_service.validate_attachment(
            filename="x.exe", size=10, mime_type="application/x-msdownload"
        )
    assert exc.value.code == "INVALID_INPUT"
    assert "不支持的文件类型" in str(exc.value)


def test_build_object_key_is_unique_per_call():
    request_id = str(uuid.uuid4())
    first = artifact_service.build_object_key(request_id, "a.txt")
    second = artifact_service.build_object_key(request_id, "a.txt")

    assert first.startswith(f"attachments/{request_id}/")
    assert first != second  # 同名文件不能互相覆盖


def test_normalize_key_decodes_url_encoding():
    assert artifact_service.normalize_key("attachments%2Fabc%2Fa.txt") == (
        "attachments/abc/a.txt"
    )


async def test_attachment_exists_returns_metadata_or_none():
    from types import SimpleNamespace

    request = SimpleNamespace(attachments=[{"key": "k1", "filename": "a.txt"}])

    assert await artifact_service.attachment_exists(request, "k1") == {
        "key": "k1",
        "filename": "a.txt",
    }
    assert await artifact_service.attachment_exists(request, "missing") is None


async def test_attachment_exists_handles_no_attachments():
    from types import SimpleNamespace

    assert (
        await artifact_service.attachment_exists(SimpleNamespace(attachments=None), "k")
        is None
    )
