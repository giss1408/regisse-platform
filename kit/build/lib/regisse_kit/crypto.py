"""Field encryption, keyed hashes, passwords, random tokens.

Wire formats are identical to flariane's `app/security/crypto.py` so data already stored by
flariane stays readable when it adopts the kit (see tests/test_flariane_parity.py).
"""

import base64
import hashlib
import hmac
import os
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def _decode_key(key_b64: str, name: str) -> bytes:
    raw = base64.b64decode(key_b64)
    if len(raw) != 32:
        raise ValueError(f"{name} must be 32 bytes, base64-encoded")
    return raw


class FieldCipher:
    """AES-256-GCM field-level encryption. Output = 12-byte nonce || ciphertext+tag."""

    def __init__(self, key_b64: str) -> None:
        self._aead = AESGCM(_decode_key(key_b64, "field encryption key"))

    def encrypt(self, plaintext: str) -> bytes:
        nonce = os.urandom(12)
        return nonce + self._aead.encrypt(nonce, plaintext.encode(), None)

    def decrypt(self, blob: bytes) -> str:
        return self._aead.decrypt(blob[:12], blob[12:], None).decode()


class KeyedHasher:
    """Deterministic HMAC-SHA256 for lookups (phone numbers) and short secrets (OTP, pickup codes).

    `purpose` separates domains so a hash from one use cannot be replayed in another.
    """

    def __init__(self, key_b64: str) -> None:
        self._key = _decode_key(key_b64, "hash key")

    def hash(self, value: str, purpose: str) -> str:
        return hmac.new(self._key, f"{purpose}:{value}".encode(), hashlib.sha256).hexdigest()

    def phone(self, phone_e164: str) -> str:
        return self.hash(phone_e164, "phone")


_ph = PasswordHasher()


def hash_password(password: str) -> str:
    """argon2id. CPU-bound: call it in a thread from async code."""
    return _ph.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _ph.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _ph.check_needs_rehash(password_hash)


def token_hash(token: str) -> str:
    """SHA-256 of a high-entropy random token (refresh tokens). Not for passwords or short codes."""
    return hashlib.sha256(token.encode()).hexdigest()


def random_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def random_digits(n: int) -> str:
    return "".join(secrets.choice("0123456789") for _ in range(n))


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())
