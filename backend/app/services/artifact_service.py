"""制品/附件服务（T045）：MinIO 客户端 + 预签名 PUT/GET URL + 大小/类型校验。

对齐 contracts/api.md 附件部分与 data-model.md 的 R-05 决策：
- 附件元数据存 requests.attachments（jsonb），文件体在 MinIO
- 预签名 PUT：客户端直传对象存储，不经应用服务器
- 大小 ≤20MB，mime 在白名单内（config.allowed_attachment_types）
"""

import uuid
from datetime import timedelta
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
