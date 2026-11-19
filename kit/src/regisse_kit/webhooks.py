"""Webhook signature verification."""

import hashlib
import hmac


def verify_hmac_sha256(header: str | None, body: bytes, secret: str, prefix: str = "sha256=") -> bool:
    """Header like `sha256=<hex HMAC of the raw body>` (Meta WhatsApp `X-Hub-Signature-256`).

    Always compare against the RAW body bytes, before any JSON parsing.
    """
    if not header or not header.startswith(prefix) or not secret:
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    given = header.removeprefix(prefix)
    return given.isascii() and hmac.compare_digest(expected.encode(), given.encode())
