"""Owner-scoped MCP server credential operations."""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.core.config import settings
from app.core.secrets import CredentialCipher
from app.models.external_credential import ExternalCredential

_ALLOWED_HEADERS = {"authorization", "x-api-key"}


def credential_cipher() -> CredentialCipher:
    return CredentialCipher(settings.mcp_credential_key, key_version="v1")


def _validate_header(kind: str, header_name: str | None) -> str:
    header = (header_name or ("authorization" if kind == "bearer" else "x-api-key")).strip().lower()
    if header not in _ALLOWED_HEADERS:
        raise AppError("INVALID_INPUT", "仅允许 Authorization 或 X-API-Key 凭证头")
    if kind == "bearer" and header != "authorization":
        raise AppError("INVALID_INPUT", "Bearer 凭证必须使用 Authorization 头")
    return header


async def create_credential(
    db: AsyncSession,
    *,
    server_id: str,
    owner_id: str,
    created_by: str,
    label: str,
    kind: str,
    value: str,
    header_name: str | None = None,
) -> ExternalCredential:
    if kind not in {"bearer", "api_key"} or not value or len(value) > 8192:
        raise AppError("INVALID_INPUT", "凭证类型或长度无效")
    cipher = credential_cipher()
    encrypted = cipher.encrypt(value)
    credential = ExternalCredential(
        server_id=server_id,
        scope_owner_id=owner_id,
        label=label.strip()[:100],
        kind=kind,
        header_name=_validate_header(kind, header_name),
        ciphertext=encrypted.ciphertext,
        key_version=encrypted.key_version,
        status="active",
        created_by=created_by,
    )
    db.add(credential)
    await db.flush()
    return credential


async def rotate_credential(
    db: AsyncSession,
    *,
    old_credential: ExternalCredential,
    created_by: str,
    label: str,
    value: str,
) -> ExternalCredential:
    if old_credential.status != "active":
        raise AppError("INVALID_INPUT", "只能轮换活动凭证")
    old_credential.status = "superseded"
    old_credential.rotated_at = datetime.now(UTC)
    return await create_credential(
        db,
        server_id=old_credential.server_id,
        owner_id=old_credential.scope_owner_id,
        created_by=created_by,
        label=label,
        kind=old_credential.kind,
        value=value,
        header_name=old_credential.header_name,
    )


async def revoke_credential(db: AsyncSession, credential: ExternalCredential) -> None:
    if credential.status == "revoked":
        return
    credential.status = "revoked"
    credential.rotated_at = datetime.now(UTC)
    await db.flush()


async def load_credential_value(
    db: AsyncSession, *, credential_id: str, owner_id: str, server_id: str
) -> tuple[str, str]:
    credential = await db.scalar(
        select(ExternalCredential).where(
            ExternalCredential.id == credential_id,
            ExternalCredential.scope_owner_id == owner_id,
            ExternalCredential.server_id == server_id,
            ExternalCredential.status == "active",
        )
    )
    if credential is None:
        raise AppError("FORBIDDEN", "外部工具凭证不可用")
    if credential.key_version != "v1":
        raise AppError("INVALID_STATE", "外部工具凭证密钥版本不可用")
    value = credential_cipher().decrypt(
        __import__("app.core.secrets", fromlist=["EncryptedCredential"]).EncryptedCredential(
            ciphertext=credential.ciphertext, key_version=credential.key_version
        )
    )
    credential.last_used_at = datetime.now(UTC)
    return credential.header_name, value
