"""`artifact_service.put_bytes` 测试。

重点验证四件事，都是「错了也不会立刻炸、但会留下隐患」的点：
1. 键前缀必须含 run_id/execution_id（否则按执行清理会误删他人制品）
2. sha256 必须由服务端算出且与内容一致（不采信沙箱自报）
3. 文件名里的 `../` 不能逃出前缀（沙箱脚本可写任意文件名）
4. 空内容与超限必须报错（静默写 0 字节对象最难排查）
"""

import hashlib
import io

import pytest

from app.api.errors import AppError
from app.services import artifact_service
from app.services.artifact_service import build_artifact_key, put_bytes

RUN_ID = "11111111-1111-1111-1111-111111111111"
EXEC_ID = "22222222-2222-2222-2222-222222222222"


class _FakeMinio:
    """记录 put_object 调用，不真连 MinIO。"""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def bucket_exists(self, _bucket: str) -> bool:
        return True

    def put_object(self, bucket, key, data, length, content_type=None):
        payload = data if isinstance(data, bytes) else data.read()
        self.calls.append(
            {
                "bucket": bucket,
                "key": key,
                "length": length,
                "content_type": content_type,
                "payload": payload,
            }
        )


@pytest.fixture
def fake_minio(monkeypatch: pytest.MonkeyPatch) -> _FakeMinio:
    fake = _FakeMinio()
    monkeypatch.setattr(artifact_service, "_minio_client", lambda: fake)
    monkeypatch.setattr(artifact_service, "_ensure_bucket", lambda: None)
    return fake


class TestObjectKey:
    def test_key_contains_run_and_execution(self) -> None:
        key = build_artifact_key(RUN_ID, EXEC_ID, "result.csv")
        assert key == f"artifacts/{RUN_ID}/{EXEC_ID}/result.csv"

    @pytest.mark.parametrize(
        "filename",
        ["../../etc/passwd", "..\\..\\windows\\x.txt", "/abs/path/a.txt", "a/b/../c.txt"],
    )
    def test_path_traversal_is_stripped(self, filename: str) -> None:
        """沙箱脚本能写任意文件名，`../` 必须被削掉，否则键会逃出执行前缀。"""
        key = build_artifact_key(RUN_ID, EXEC_ID, filename)
        assert key.startswith(f"artifacts/{RUN_ID}/{EXEC_ID}/")
        assert ".." not in key.split("/")[-1]

    def test_empty_filename_falls_back(self) -> None:
        key = build_artifact_key(RUN_ID, EXEC_ID, "")
        assert key.endswith("/unnamed")


class TestPutBytes:
    def test_returns_key_and_correct_sha256(self, fake_minio: _FakeMinio) -> None:
        data = b"hello sandbox"
        key, digest = put_bytes(RUN_ID, EXEC_ID, "a.txt", data)

        assert key == f"artifacts/{RUN_ID}/{EXEC_ID}/a.txt"
        assert digest == hashlib.sha256(data).hexdigest()
        assert fake_minio.calls[0]["payload"] == data
        assert fake_minio.calls[0]["length"] == len(data)

    def test_stream_input_sha256_matches(self, fake_minio: _FakeMinio) -> None:
        data = b"x" * 100_000
        _key, digest = put_bytes(RUN_ID, EXEC_ID, "big.bin", io.BytesIO(data))

        assert digest == hashlib.sha256(data).hexdigest()
        assert fake_minio.calls[0]["length"] == len(data)
        assert fake_minio.calls[0]["payload"] == data

    def test_mime_default(self, fake_minio: _FakeMinio) -> None:
        put_bytes(RUN_ID, EXEC_ID, "a.bin", b"1")
        assert fake_minio.calls[0]["content_type"] == "application/octet-stream"

    def test_mime_passed_through(self, fake_minio: _FakeMinio) -> None:
        put_bytes(RUN_ID, EXEC_ID, "a.csv", b"1,2", mime="text/csv")
        assert fake_minio.calls[0]["content_type"] == "text/csv"

    def test_empty_bytes_rejected(self, fake_minio: _FakeMinio) -> None:
        with pytest.raises(AppError) as exc:
            put_bytes(RUN_ID, EXEC_ID, "empty.txt", b"")
        assert exc.value.code == "INVALID_INPUT"
        assert fake_minio.calls == []

    def test_empty_stream_rejected(self, fake_minio: _FakeMinio) -> None:
        with pytest.raises(AppError):
            put_bytes(RUN_ID, EXEC_ID, "empty.txt", io.BytesIO(b""))
        assert fake_minio.calls == []

    def test_oversize_bytes_rejected(
        self, fake_minio: _FakeMinio, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(artifact_service.settings, "sbx_artifact_max_file_mb", 1)
        with pytest.raises(AppError) as exc:
            put_bytes(RUN_ID, EXEC_ID, "big.bin", b"x" * (1024 * 1024 + 1))
        assert "单文件上限" in str(exc.value)
        assert fake_minio.calls == []

    def test_oversize_stream_rejected(
        self, fake_minio: _FakeMinio, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """流式路径也要早退，不能先读完整个大文件再判断。"""
        monkeypatch.setattr(artifact_service.settings, "sbx_artifact_max_file_mb", 1)
        with pytest.raises(AppError):
            put_bytes(RUN_ID, EXEC_ID, "big.bin", io.BytesIO(b"x" * (1024 * 1024 + 1)))
        assert fake_minio.calls == []

    def test_exactly_at_limit_accepted(
        self, fake_minio: _FakeMinio, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """边界值：正好等于上限应通过（上限是「不超过」而非「小于」）。"""
        monkeypatch.setattr(artifact_service.settings, "sbx_artifact_max_file_mb", 1)
        data = b"x" * (1024 * 1024)
        _, digest = put_bytes(RUN_ID, EXEC_ID, "edge.bin", data)
        assert digest == hashlib.sha256(data).hexdigest()
