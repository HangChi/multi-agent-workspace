"""Secret-safe audit helpers for external tool lifecycle operations."""

from __future__ import annotations

import hashlib
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit

EXTERNAL_AUDIT_ACTIONS = frozenset(
    {
        "external_server_register",
        "external_server_validate",
        "external_tool_approve",
        "external_tool_reject",
        "external_tool_pause",
        "external_tool_resume",
        "external_tool_revoke",
        "external_tool_version_upgrade",
        "external_tool_install",
        "external_tool_grant",
        "external_tool_grant_revoke",
        "external_credential_rotate",
        "external_credential_revoke",
        "external_tool_invocation",
    }
)
_SENSITIVE_KEYS = {
    "authorization",
    "token",
    "secret",
    "password",
    "credential",
    "api_key",
    "api-key",
}


def _safe_value(value: Any, *, key: str = "", depth: int = 0) -> Any:
    if depth > 4:
        return "[truncated]"
    if any(marker in key.lower() for marker in _SENSITIVE_KEYS):
        return "[redacted]"
    if isinstance(value, dict):
        return {
            str(k)[:80]: _safe_value(v, key=str(k), depth=depth + 1)
            for k, v in list(value.items())[:50]
        }
    if isinstance(value, (list, tuple)):
        return [_safe_value(item, depth=depth + 1) for item in value[:50]]
    if isinstance(value, str):
        return value[:512]
    if value is None or isinstance(value, (int, float, bool)):
        return value
    return str(value)[:128]


def payload_hash(value: Any) -> str:
    """Return a stable digest without persisting the original payload."""
    import json

    serialized = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


async def write_external_audit(
    db: AsyncSession,
    *,
    user_id: str,
    action: str,
    target_type: str,
    target_id: str,
    detail: dict[str, Any],
) -> None:
    if action not in EXTERNAL_AUDIT_ACTIONS:
        raise ValueError("unknown external-tool audit action")
    await write_audit(
        db,
        user_id=user_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        detail=_safe_value(detail),
        strict=True,
    )
