"""The key rings of this service (field encryption and lookup hashes): one place, built from settings."""

from functools import lru_cache

from regisse_kit.keyring import VersionedCipher, VersionedHasher

from app.config import get_settings


@lru_cache
def _cipher(ring: tuple, current: str) -> VersionedCipher:
    return VersionedCipher(dict(ring), current, legacy_kid="0")


@lru_cache
def _hasher(ring: tuple, current: str) -> VersionedHasher:
    return VersionedHasher(dict(ring), current)


def cipher() -> VersionedCipher:
    s = get_settings()
    return _cipher(tuple(sorted(s.field_keyring.items())), s.field_key_current)


def hasher() -> VersionedHasher:
    s = get_settings()
    return _hasher(tuple(sorted(s.hash_keyring.items())), s.hash_key_current)
