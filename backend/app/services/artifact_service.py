"""制品/附件服务：MinIO 客户端 + 预签名 URL + 服务端直传。

两条上传路径，用途不同：
- **预签名 PUT**（`presign_put`）：附件，客户端直传对象存储，不经应用服务器
- **服务端直传**（`put_bytes`）：沙箱产出，Worker 侧读取容器归档后写入，
  同时算 sha256——客户端不可信，指纹必须由服务端计算

对齐 contracts/api.md 附件部分与 data-model.md 的 R-05 决策。
"""

import hashlib
import io
import uuid
from datetime import timedelta
from typing import BinaryIO, cast
from urllib.parse import unquote

from minio import Minio
from minio.error import S3Error

from app.api.errors import AppError
from app.core.config import settings
from app.models.request import Request
from app.schemas.request import AttachmentUploadOut

# 预签名有效期
_PUT_EXPIRY = timedelta(minutes=15)
_GET_EXPIRY = timedelta(hours=1)


def _minio_client() -> Minio:
    """懒创建 MinIO 客户端（避免模块加载时连接）。"""
    return Minio(
        settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=settings.minio_secure,
    )


def _ensure_bucket() -> None:
    """确保 bucket 存在（幂等）。"""
    client = _minio_client()
    if not client.bucket_exists(settings.minio_bucket):
        client.make_bucket(settings.minio_bucket)


def validate_attachment(*, filename: str, size: int, mime_type: str) -> None:
    """大小（≤20MB）与 mime 白名单校验，失败抛 400 INVALID_INPUT。"""
    if not filename or not filename.strip():
        raise AppError("INVALID_INPUT", "文件名不能为空")
    max_bytes = settings.max_attachment_size_mb * 1024 * 1024
    if size <= 0 or size > max_bytes:
        raise AppError(
            "INVALID_INPUT",
            f"附件大小需在 1B ~ {settings.max_attachment_size_mb}MB 之间",
        )
    if mime_type not in settings.allowed_attachment_type_list:
        raise AppError("INVALID_INPUT", f"不支持的文件类型: {mime_type}")


def build_object_key(request_id: str, filename: str) -> str:
    """对象键：attachments/{request_id}/{uuid}-{filename}。"""
    return f"attachments/{request_id}/{uuid.uuid4().hex}-{filename}"


def presign_put(request_id: str, *, filename: str, size: int, mime_type: str) -> AttachmentUploadOut:
    """校验 + 生成预签名 PUT URL（不落库，落库由端点回填元数据）。"""
    validate_attachment(filename=filename, size=size, mime_type=mime_type)
    _ensure_bucket()
    key = build_object_key(request_id, filename)
    url = _minio_client().presigned_put_object(
        settings.minio_bucket, key, expires=_PUT_EXPIRY
    )
    return AttachmentUploadOut(
        key=key, name=filename, size=size, mime=mime_type, presigned_put_url=url
    )


def presign_get(key: str) -> str:
    """生成预签名 GET URL（下载）。"""
    _ensure_bucket()
    return _minio_client().presigned_get_object(
        settings.minio_bucket, key, expires=_GET_EXPIRY
    )


async def attachment_exists(request: Request, key: str) -> dict | None:
    """在 requests.attachments 元数据中查找附件；找不到抛 404。"""
    for att in request.attachments or []:
        if att.get("key") == key:
            return att
    return None


def normalize_key(raw_key: str) -> str:
    """路径参数中的 key 可能带编码/前缀，统一还原。"""
    return unquote(raw_key)


def _object_exists(key: str) -> bool:
    """MinIO 对象是否存在（下载时二次确认）。"""
    try:
        _minio_client().stat_object(settings.minio_bucket, key)
        return True
    except S3Error:
        return False


def download_url(request: Request, key: str) -> str:
    """校验归属与元数据存在后返回预签名 GET URL。"""
    att = None
    for item in request.attachments or []:
        if item.get("key") == key:
            att = item
            break
    if att is None:
        raise AppError("NOT_FOUND", "附件不存在")
    if not _object_exists(key):
        raise AppError("NOT_FOUND", "附件对象不存在")
    return presign_get(key)


# ---------------------------------------------------------------------------
# 沙箱产出（服务端直传）
# ---------------------------------------------------------------------------

# 单文件上限与 settings 里的一致，但这里单独取一次便于函数内早退，
# 避免在流式循环里反复读配置对象。
def build_artifact_key(run_id: str, execution_id: str, filename: str) -> str:
    """沙箱制品对象键：`artifacts/{run_id}/{execution_id}/{filename}`。

    不加随机前缀（与附件不同）：同一执行内同名文件由调用方追加序号去重，
    保持键可预测，便于按执行前缀列出与批量清理。
    """
    # 去掉路径分隔符，防止文件名里的 `../` 逃出前缀
    safe = filename.replace("\\", "/").split("/")[-1] or "unnamed"
    return f"artifacts/{run_id}/{execution_id}/{safe}"


def put_bytes(
    run_id: str,
    execution_id: str,
    filename: str,
    data: bytes | io.IOBase,
    mime: str | None = None,
) -> tuple[str, str]:
    """服务端直传一个制品到对象存储，返回 `(object_key, sha256)`。

    Args:
        run_id: 所属 Run。
        execution_id: 产生该制品的沙箱执行。
        filename: 文件名（会去掉路径部分，防止 `../` 逃逸前缀）。
        data: 文件内容；传 `bytes` 或可读流。流式传入时按块读取并同步算指纹，
            避免把大文件整个读进内存（单文件上限 20MB，但并发时会放大）。
        mime: MIME 类型，未知则留空。

    Returns:
        (object_key, sha256_hex)

    Raises:
        AppError: 内容为空或超过单文件上限 → `INVALID_INPUT`。

    sha256 由**服务端**计算，不采信调用方传入的指纹：沙箱里的脚本不可信，
    让它自报指纹等于把完整性校验交给被校验方。
    """
    max_bytes = settings.sbx_artifact_max_file_mb * 1024 * 1024

    hasher = hashlib.sha256()
    if isinstance(data, bytes):
        if not data:
            raise AppError("INVALID_INPUT", "制品内容为空")
        if len(data) > max_bytes:
            raise AppError(
                "INVALID_INPUT",
                f"制品超过单文件上限 {settings.sbx_artifact_max_file_mb}MB: {filename}",
            )
        hasher.update(data)
        # MinIO's runtime implementation calls ``read`` on the body even though
        # the type annotation is BinaryIO; wrap bytes instead of relying on the
        # SDK accepting raw bytes.
        body: BinaryIO = io.BytesIO(data)
        length = len(data)
    else:
        # 流式：先完整读一遍算指纹与长度，再 seek(0) 供上传。
        # 这样能精确拿到长度（MinIO put_object 需要 length），且不必二次缓冲整份内容。
        total = 0
        while chunk := data.read(1024 * 1024):
            total += len(chunk)
            if total > max_bytes:
                raise AppError(
                    "INVALID_INPUT",
                    f"制品超过单文件上限 {settings.sbx_artifact_max_file_mb}MB: {filename}",
                )
            hasher.update(chunk)
        if total == 0:
            raise AppError("INVALID_INPUT", "制品内容为空")
        data.seek(0)
        body = cast(BinaryIO, data)
        length = total

    key = build_artifact_key(run_id, execution_id, filename)
    _ensure_bucket()
    _minio_client().put_object(
        settings.minio_bucket,
        key,
        body,
        length=length,
        content_type=mime or "application/octet-stream",
    )
    return key, hasher.hexdigest()


def artifact_get_url(key: str) -> str:
    """制品的预签名下载 URL。与附件走同一套 `presign_get`。"""
    return presign_get(key)
