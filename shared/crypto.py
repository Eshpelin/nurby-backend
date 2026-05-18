"""Symmetric encryption helpers for sensitive secrets at rest.

Uses Fernet (AES-128-CBC + HMAC-SHA256) keyed by a SHA-256 derivation
of ``settings.jwt_secret`` (urlsafe-b64 of the 32-byte digest). We use
``jwt_secret`` because the config module has no ``secret_key`` field
and reusing it avoids introducing a new env var; the same value also
gates JWT signing, so its operational secrecy is already required.

If the operator rotates ``jwt_secret``, previously encrypted blobs
become unreadable (Fernet ``InvalidToken``). That is desirable for
JWTs but means stored Telegram bot tokens must be re-entered after a
rotation. Document this in any future ops guide.
"""

from __future__ import annotations

import base64
import functools
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from shared.config import settings


@functools.lru_cache(maxsize=1)
def _cipher() -> Fernet:
    """Build and memoize a Fernet cipher derived from jwt_secret."""
    digest = hashlib.sha256(settings.jwt_secret.encode("utf-8")).digest()
    key = base64.urlsafe_b64encode(digest)
    return Fernet(key)


def encrypt_secret(plaintext: str) -> bytes:
    """Encrypt a UTF-8 string. Returns the Fernet token as bytes."""
    if not isinstance(plaintext, str):
        raise TypeError("encrypt_secret expects str")
    return _cipher().encrypt(plaintext.encode("utf-8"))


def decrypt_secret(ciphertext: bytes) -> str:
    """Decrypt a previously stored Fernet token. Raises InvalidToken on
    tamper or key mismatch (e.g. jwt_secret was rotated)."""
    if isinstance(ciphertext, memoryview):
        ciphertext = bytes(ciphertext)
    if not isinstance(ciphertext, (bytes, bytearray)):
        raise TypeError("decrypt_secret expects bytes")
    return _cipher().decrypt(bytes(ciphertext)).decode("utf-8")


__all__ = ["encrypt_secret", "decrypt_secret", "InvalidToken"]
