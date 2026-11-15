from dataclasses import replace

from app.core.secrets import CredentialCipher


def test_credential_cipher_round_trip_and_rotation_metadata():
    cipher = CredentialCipher("test-key-material")
    encrypted = cipher.encrypt("canary-secret")
    assert encrypted.key_version == cipher.key_version
    assert encrypted.ciphertext != "canary-secret"
    assert cipher.decrypt(encrypted) == "canary-secret"
    rotated = cipher.rotate(encrypted, "new-secret")
    assert rotated.key_version == cipher.key_version
    assert cipher.decrypt(rotated) == "new-secret"


def test_credential_cipher_rejects_tampering():
    cipher = CredentialCipher("test-key-material")
    encrypted = cipher.encrypt("canary-secret")
    tampered = replace(encrypted, ciphertext=encrypted.ciphertext[:-2] + "xx")
    try:
        cipher.decrypt(tampered)
    except ValueError as exc:
        assert "decrypt" in str(exc).lower()
    else:
        raise AssertionError("tampered ciphertext must fail closed")
