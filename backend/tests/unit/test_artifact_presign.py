"""附件预签名：bucket 探测缓存 + MinIO 调用不阻塞事件循环。

回归点（2026-11-17 审计）：
- `_ensure_bucket()` 原先每次预签名都 HEAD 一次对象存储，而预签名是纯本地签名；
  一个下载请求因此要付 2~3 次同步 HTTP，且发生在 async 端点里 = 阻塞事件循环。
- `download_url()` 原先直接遍历 ORM 对象，无法安全地丢进线程池。
"""

from __future__ import annotations

import asyncio
import threading

import pytest

from app.api.errors import AppError
from app.services import artifact_service


class _FakeMinio:
    def __init__(self) -> None:
        self.bucket_probes = 0
        self.created = 0
        self.presign_calls = 0
        self.stat_calls = 0

    def bucket_exists(self, _bucket: str) -> bool:
        self.bucket_probes += 1
        return True

    def make_bucket(self, _bucket: str) -> None:
        self.created += 1

    def presigned_get_object(self, _bucket, key, expires=None) -> str:
        self.presign_calls += 1
        return f"https://objects.example.test/{key}"

    def stat_object(self, _bucket, _key) -> None:
        self.stat_calls += 1


@pytest.fixture
def fake_minio(monkeypatch: pytest.MonkeyPatch) -> _FakeMinio:
    fake = _FakeMinio()
    monkeypatch.setattr(artifact_service, "_minio_client", lambda: fake)
    monkeypatch.setattr(artifact_service, "_bucket_ready", False)
    return fake


def test_bucket_is_probed_once_per_process(fake_minio: _FakeMinio) -> None:
    artifact_service._ensure_bucket()
    artifact_service._ensure_bucket()
    artifact_service._ensure_bucket()

    assert fake_minio.bucket_probes == 1


def test_reset_bucket_probe_allows_reprobe(fake_minio: _FakeMinio) -> None:
    artifact_service._ensure_bucket()
    artifact_service.reset_bucket_probe()
    artifact_service._ensure_bucket()

    assert fake_minio.bucket_probes == 2


def test_download_url_accepts_plain_attachment_dicts(fake_minio: _FakeMinio) -> None:
    attachments = [{"key": "attachments/r1/a.txt", "name": "a.txt"}]

    url = artifact_service.download_url(attachments, "attachments/r1/a.txt")

    assert url.endswith("attachments/r1/a.txt")
    assert fake_minio.stat_calls == 1


def test_download_url_rejects_unknown_key(fake_minio: _FakeMinio) -> None:
    with pytest.raises(AppError):
        artifact_service.download_url([{"key": "other"}], "attachments/r1/a.txt")

    assert fake_minio.stat_calls == 0


def test_async_wrappers_run_off_the_event_loop(fake_minio: _FakeMinio) -> None:
    """MinIO 是同步 SDK：包装后必须在工作线程执行，不能占住事件循环线程。"""
    seen: list[int] = []
    original = artifact_service.presign_get

    def recording(key: str) -> str:
        seen.append(threading.get_ident())
        return original(key)

    artifact_service.presign_get = recording  # type: ignore[assignment]
    try:

        async def scenario() -> tuple[int, str]:
            loop_thread = threading.get_ident()
            url = await artifact_service.presign_get_async("k")
            return loop_thread, url

        loop_thread, url = asyncio.run(scenario())
    finally:
        artifact_service.presign_get = original  # type: ignore[assignment]

    assert url.endswith("/k")
    assert seen and seen[0] != loop_thread
