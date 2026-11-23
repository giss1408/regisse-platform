"""Versioned keys: rotate the field-encryption key and the lookup-hash key without a big-bang re-encryption.

Encrypted fields get an envelope that names the key: `b"RK1" | len(kid) | kid | nonce | ciphertext+tag`, with the header authenticated (AAD),
so the key id cannot be swapped. Blobs written before versioning (`nonce | ciphertext+tag`, flariane/kit wire format) are read with the
`legacy_kid` key, so turning this on needs no data migration; a rotation job re-encrypts at its own pace.

Lookup hashes (phone, email) are deterministic, so they cannot carry a key id. A lookup therefore tries every key of the ring
(`phone_all`, current first) and a rotation job rewrites stored hashes under the current key. One-time codes also verify against every key,
so codes issued just before a rotation still work.

Retire an old key only when the rotation job reports nothing left under it (`auth.cli rotate-data-keys --verify`).
"""

import base64
import hashlib
import hmac
import os
import re
from collections.abc import Mapping

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC = b"RK1"
KID = re.compile(r"^[A-Za-z0-9_-]{1,16}$")


class UnknownKey(Exception):
    """The blob names a key that is not in the ring (retired too early, or a wrong environment)."""


def _ring(keys: Mapping[str, str], current: str, what: str) -> dict[str, bytes]:
    if current not in keys:
        raise ValueError(f"current {what} key id {current!r} is not in the ring")
    out: dict[str, bytes] = {}
    for kid, b64 in keys.items():
        if not KID.match(kid):
            raise ValueError(f"bad {what} key id {kid!r}")
        raw = base64.b64decode(b64)
        if len(raw) != 32:
            raise ValueError(f"{what} key {kid!r} must be 32 bytes, base64-encoded")
        out[kid] = raw
    return out


class VersionedCipher:
    def __init__(self, keys: Mapping[str, str], current: str, legacy_kid: str | None = "0") -> None:
        self._raw = _ring(keys, current, "field")
        self._aead = {kid: AESGCM(raw) for kid, raw in self._raw.items()}
        self.current = current
        self._legacy = legacy_kid if legacy_kid in self._aead else None

    @staticmethod
    def _header(kid: str) -> bytes:
        k = kid.encode()
        return MAGIC + bytes([len(k)]) + k

    def encrypt(self, plaintext: str) -> bytes:
        header, nonce = self._header(self.current), os.urandom(12)
        return header + nonce + self._aead[self.current].encrypt(nonce, plaintext.encode(), header)

    def _parse(self, blob: bytes) -> tuple[str, bytes] | None:
        """(kid, header) of an envelope, or None for a legacy blob."""
        if blob[:3] != MAGIC or len(blob) < 5:
            return None
        n = blob[3]
        if len(blob) < 4 + n + 12 + 16:
            return None
        try:
            kid = blob[4 : 4 + n].decode()
        except UnicodeDecodeError:
            return None
        return kid, blob[: 4 + n]

    def kid_of(self, blob: bytes) -> str | None:
        """Key id of an envelope; None for a legacy (unversioned) blob."""
        parsed = self._parse(blob)
        return parsed[0] if parsed else None

    def decrypt(self, blob: bytes) -> str:
        parsed = self._parse(blob)
        if parsed is not None:
            kid, header = parsed
            aead = self._aead.get(kid)
            if aead is not None:
                body = blob[len(header) :]
                try:
                    return aead.decrypt(body[:12], body[12:], header).decode()
                except Exception:  # noqa: BLE001: a legacy blob can start with the magic by chance (2^-24): try that reading too
                    if self._legacy is None:
                        raise
            elif self._legacy is None:
                raise UnknownKey(kid)
        if self._legacy is None:
            raise UnknownKey("legacy")
        return self._aead[self._legacy].decrypt(blob[:12], blob[12:], None).decode()

    def is_current(self, blob: bytes) -> bool:
        return self.kid_of(blob) == self.current

    def reencrypt(self, blob: bytes) -> bytes:
        return self.encrypt(self.decrypt(blob))


class VersionedHasher:
    """Same output as `KeyedHasher` for each key; `*_all` lists the hashes under every key, current first."""

    def __init__(self, keys: Mapping[str, str], current: str) -> None:
        self._keys = _ring(keys, current, "hash")
        self.current = current
        self._order = [current] + [k for k in self._keys if k != current]

    def _h(self, kid: str, value: str, purpose: str) -> str:
        return hmac.new(self._keys[kid], f"{purpose}:{value}".encode(), hashlib.sha256).hexdigest()

    def hash(self, value: str, purpose: str) -> str:
        return self._h(self.current, value, purpose)

    def phone(self, phone_e164: str) -> str:
        return self.hash(phone_e164, "phone")

    def hash_all(self, value: str, purpose: str) -> list[str]:
        return [self._h(k, value, purpose) for k in self._order]

    def phone_all(self, phone_e164: str) -> list[str]:
        return self.hash_all(phone_e164, "phone")
