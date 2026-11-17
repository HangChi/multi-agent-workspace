"""制品/附件服务：MinIO 客户端 + 预签名 URL + 服务端直传。

两条上传路径，用途不同：
- **POST Policy**（`presign_upload`）：附件，客户端直传对象存储，不经应用服务器。
  用 policy 而非预签名 PUT，是因为只有 policy 能把大小上限交给存储端强制。
- **服务端直传**（`put_bytes`）：沙箱产出，Worker 侧读取容器归档后写入，
  同时算 sha256——客户端不可信，指纹必须由服务端计算

下载侧仍是预签名 GET（`presign_get`）。它把 Host 签进签名里，因此无法像
上传那样事后替换成对外地址——见该函数注释里的已知限制。

对齐 contracts/api.md 附件部分与 data-model.md 的 R-05 决策。
"""

import asyncio
import hashlib
import io
import uuid
from datetime import UTC, datetime, timedelta
from typing import BinaryIO, cast
from urllib.parse import unquote

from minio import Minio
from minio.datatypes import PostPolicy
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


# bucket 存在性只探测一次：原先每次预签名都 HEAD 一次，而预签名本身是纯本地
# 计算 —— 一个「对象存储还在不在」的探针被放进了每个下载请求的关键路径。
_bucket_ready = False


def _ensure_bucket() -> None:
    """确保 bucket 存在（幂等，进程内只探测一次）。"""
    global _bucket_ready
    if _bucket_ready:
        return
    client = _minio_client()
    if not client.bucket_exists(settings.minio_bucket):
        client.make_bucket(settings.minio_bucket)
    _bucket_ready = True


def reset_bucket_probe() -> None:
    """重置 bucket 探测标记（测试用：换掉 MinIO 假客户端后需要重新探测）。"""
    global _bucket_ready
    _bucket_ready = False


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


def presign_upload(
    request_id: str, *, filename: str, size: int, mime_type: str
) -> AttachmentUploadOut:
    """校验 + 生成 POST Policy 上传凭据（不落库，落库由端点回填元数据）。

    ⚠️ 用 POST Policy 而非预签名 PUT，是为了让**对象存储本身**强制大小上限。

    预签名 PUT（`presigned_put_object`）无法携带任何长度条件：S3 协议里长度
    约束只存在于 policy 的 `content-length-range`。此前只校验客户端**声明**的
    `size`，攻击者可以声明 1 字节、实际 PUT 任意大小 —— `MAX_ATTACHMENT_SIZE_MB`
    形同虚设。改成 POST Policy 后，超限请求由 MinIO 直接返回
    `400 EntityTooLarge`，不依赖客户端自觉。

    `content-length-range` 的下限取 1：空文件没有意义，且能在存储层一并拒绝。
    """
    validate_attachment(filename=filename, size=size, mime_type=mime_type)
    _ensure_bucket()

    max_bytes = settings.max_attachment_size_mb * 1024 * 1024
    key = build_object_key(request_id, filename)

    policy = PostPolicy(settings.minio_bucket, datetime.now(UTC) + _PUT_EXPIRY)
    policy.add_content_length_range_condition(1, max_bytes)
    # 键名钉死，防止客户端改路径把附件写到别处（覆盖他人对象/逃出 attachments 前缀）。
    #
    # 不钉 `$Content-Type`：multipart 表单的每一部分自带 content-type，与 policy 里的
    # 等值条件极易冲突（实测直接 403 Policy Condition failed），而且客户端本来就能
    # 自由声明 content-type —— 钉它既拦不住伪造、又制造故障。
    # 实际类型由 validate_attachment 的白名单决定（那才是权威判断点）。
    policy.add_equals_condition("$key", key)

    fields = _minio_client().presigned_post_policy(policy)
    # `key` 不在返回的签名字段里，但 policy 已为它设定等值条件，
    # 客户端必须把它随表单一起提交，否则存储端报 "Policy Condition failed"。
    fields["key"] = key

    return AttachmentUploadOut(
        key=key,
        name=filename,
        size=size,
        mime=mime_type,
        upload_url=f"{_upload_base_url()}/{settings.minio_bucket}",
        upload_fields=fields,
    )


def _upload_base_url() -> str:
    """对象存储的**浏览器可访问**基址（客户端直连上传用）。

    ⚠️ 必须用 `minio_public_endpoint` 而不是 `minio_endpoint`。
    后者是 compose 内的服务名（minio:9000），只有 API/Worker 能解析；
    把它塞进返回给浏览器的 URL，前端上传会直接 DNS 失败（此前附件上传
    在浏览器里根本走不通，但容器内跑的验收脚本看不出来）。

    policy 签名只覆盖 policy 文档本身（bucket/key/条件），不含 host，
    所以这里换对外地址不影响签名有效性。
    """
    endpoint = settings.minio_public_endpoint or settings.minio_endpoint
    scheme = "https" if settings.minio_secure else "http"
    return f"{scheme}://{endpoint}"


def presign_get(key: str) -> str:
    """生成预签名 GET URL（下载）。

    ⚠️ 已知限制：签名里包含 Host。若 `MINIO_PUBLIC_ENDPOINT` 与
    `MINIO_ENDPOINT` 不一致，这里产出的仍是容器内地址（`minio:9000`），
    浏览器打不开。与上传路径不同，签名无法在事后改写 host。

    要彻底解决，需让 API 用一个「外部可达且与签名一致」的 endpoint 建客户端
    （对 MinIO 来说即把对外域名反代到 9000），或改成后端代理下载流。
    在此之前，部署时应保证 MINIO_PUBLIC_ENDPOINT 与浏览器可达地址一致，
    并让 MinIO 通过反代对外暴露。
    """
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


def download_url(attachments: list[dict], key: str) -> str:
    """校验元数据存在后返回预签名 GET URL。

    入参是 `requests.attachments` 的纯数据副本（而不是 ORM 对象）：本函数会被
    `asyncio.to_thread` 丢到工作线程执行，碰 ORM 才有线程安全隐患。
    """
    att = None
    for item in attachments:
        if item.get("key") == key:
            att = item
            break
    if att is None:
        raise AppError("NOT_FOUND", "附件不存在")
    if not _object_exists(key):
        raise AppError("NOT_FOUND", "附件对象不存在")
    return presign_get(key)


# ---------------------------------------------------------------------------
# 异步包装：MinIO SDK 是同步的，直接在 async 端点里调用会阻塞事件循环
# （API 只有 2 个 worker，并发下载时全站一起变慢）。统一丢线程池。
# ---------------------------------------------------------------------------


async def presign_upload_async(
    request_id: str, *, filename: str, size: int, mime_type: str
) -> AttachmentUploadOut:
    return await asyncio.to_thread(
        presign_upload, request_id, filename=filename, size=size, mime_type=mime_type
    )


async def presign_get_async(key: str) -> str:
    return await asyncio.to_thread(presign_get, key)


async def download_url_async(attachments: list[dict], key: str) -> str:
    return await asyncio.to_thread(download_url, attachments, key)


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
