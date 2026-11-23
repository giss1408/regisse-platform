"""One-time codes (phone verification, wallet-change confirmation, activation).

Pure logic, no storage: the product stores a `CodeChallenge` however it likes (columns on a row,
a table of challenges) and calls `issue` / `check`. Only a keyed hash of the code is kept.
Pattern ported from flariane's `services/phone_verification.py` (admin sends the code through a
wa.me link; the user types it in the app).
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from .crypto import KeyedHasher, constant_time_equals, random_digits


class CodeError(Exception):
    """Base class: callers map these to their own HTTP errors."""


class NoActiveCode(CodeError):
    pass


class TooManyAttempts(CodeError):
    pass


class WrongCode(CodeError):
    pass


@dataclass
class CodeChallenge:
    code_hash: str
    expires_at: datetime
    attempts: int = 0


def _hash(hasher: KeyedHasher, subject: str, purpose: str, code: str) -> str:
    # Bound to subject and purpose: a hash cannot be replayed for another user or another use.
    return hasher.hash(f"{subject}:{code}", f"code:{purpose}")


def issue(hasher: KeyedHasher, subject: str, purpose: str, now: datetime, ttl: timedelta, digits: int = 6) -> tuple[CodeChallenge, str]:
    """New code; the caller replaces any previous challenge (it stops working). Returns (challenge, plaintext code)."""
    code = random_digits(digits)
    return CodeChallenge(code_hash=_hash(hasher, subject, purpose, code), expires_at=now + ttl), code


def check(hasher: KeyedHasher, challenge: CodeChallenge | None, subject: str, purpose: str, code: str, now: datetime, max_attempts: int = 5) -> None:
    """Raises NoActiveCode / TooManyAttempts / WrongCode; returns None when the code is right.

    A wrong guess increments `challenge.attempts`: the caller must persist it even though the request fails.
    On success the caller deletes the challenge (single use).
    """
    if challenge is None or challenge.expires_at <= now:
        raise NoActiveCode()
    if challenge.attempts >= max_attempts:
        raise TooManyAttempts()
    # A versioned hasher (see keyring) also accepts codes issued under an older key, so a rotation does not invalidate pending codes.
    candidates = hasher.hash_all(f"{subject}:{code}", f"code:{purpose}") if hasattr(hasher, "hash_all") else [_hash(hasher, subject, purpose, code)]
    if not any([constant_time_equals(challenge.code_hash, c) for c in candidates]):
        challenge.attempts += 1
        raise WrongCode()
