"""Small encrypted-secret boundary for external MCP credentials.

The database stores only the Fernet token and key version. Callers receive a
write-only input and must decrypt only immediately before an outbound request.
"""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass

from cryptography.fernet import Fernet, InvalidToken


@dataclass(frozen=True)
class EncryptedCredential:
    ciphertext: str
    key_version: str = "v1"


class CredentialCipher:
    """Encrypt/decrypt credentials using a deterministic key-derived Fernet key."""

    key_version = "v1"

    def __init__(self, key_material: str, *, key_version: str = "v1") -> None:
        if len(key_material) < 8:
            raise ValueError("credential key material is too short")
        digest = hashlib.sha256(key_material.encode("utf-8")).digest()
        self._fernet = Fernet(base64.urlsafe_b64encode(digest))
        self.key_version = key_version

    def encrypt(self, value: str) -> EncryptedCredential:
        if not value:
            raise ValueError("credential value must not be empty")
        return EncryptedCredential(
            ciphertext=self._fernet.encrypt(value.encode("utf-8")).decode("ascii"),
            key_version=self.key_version,
        )

    def decrypt(self, value: EncryptedCredential) -> str:
        try:
            return self._fernet.decrypt(value.ciphertext.encode("ascii")).decode("utf-8")
        except (InvalidToken, UnicodeDecodeError, ValueError) as exc:
            raise ValueError("credential decrypt failed") from exc

    def rotate(self, _old: EncryptedCredential, new_value: str) -> EncryptedCredential:
        return self.encrypt(new_value)
