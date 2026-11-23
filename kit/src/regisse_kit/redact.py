"""Masks personal data and secrets in anything that is about to be logged. No dependencies (safe to import in any product).

    from regisse_kit.redact import RedactingFilter
    handler.addFilter(RedactingFilter())

Masks phone numbers (+international, 225..., 0XXXXXXXXX), JWTs, Bearer tokens, and the VALUES of secret-like keys (password, token, code, ...)
in structured fields. Free text cannot be understood, so a bare 6-digit code in a sentence is NOT masked: do not log codes.
"""

import logging
import re
from typing import Any

REDACTED = "[redacted]"
_SECRET_KEY = re.compile(r"(password|passwd|secret|token|authorization|cookie|api[_-]?key|otp)|(^|_)code$", re.I)
_NOT_SECRET_KEYS = {"status_code", "error_code"}
_PATTERNS = [
    (re.compile(r"eyJ[\w-]+\.[\w-]+\.[\w-]+"), REDACTED),  # JWT
    (re.compile(r"(?i)bearer\s+\S+"), "Bearer " + REDACTED),
    (re.compile(r"\+\d{8,15}"), "[phone]"),  # international
    (re.compile(r"(?<!\d)225\d{10}(?!\d)"), "[phone]"),  # CI, no plus
    (re.compile(r"(?<!\d)0\d{9}(?!\d)"), "[phone]"),  # CI local
]


def secret_key(key: object) -> bool:
    k = str(key)
    return k not in _NOT_SECRET_KEYS and bool(_SECRET_KEY.search(k))


def redact(value: Any) -> Any:
    if isinstance(value, str):
        for pattern, repl in _PATTERNS:
            value = pattern.sub(repl, value)
        return value
    if isinstance(value, dict):
        return {k: (REDACTED if secret_key(k) else redact(v)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    return value


class RedactingFilter(logging.Filter):
    """Rewrites the record in place: the formatted message, and string/dict extras. Never drops a record."""

    _STD = set(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {"message", "asctime"}

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg, record.args = redact(record.getMessage()), None
        for k, v in list(record.__dict__.items()):
            if k in self._STD or k.startswith("_"):
                continue
            record.__dict__[k] = REDACTED if secret_key(k) else redact(v)
        if record.exc_info and record.exc_info[1] is not None:
            # exception text is rendered later by the formatter: render it now, masked, and drop the raw tuple
            import traceback

            record.exc_text = redact("".join(traceback.format_exception(*record.exc_info)))
            record.exc_info = None
        return True
