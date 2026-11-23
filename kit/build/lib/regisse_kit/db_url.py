"""Database URL helpers."""

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def to_asyncpg_url(url: str) -> str:
    """postgres://…?sslmode=require[&channel_binding=require] -> postgresql+asyncpg://…?ssl=require.

    SQLAlchemy+asyncpg wants `postgresql+asyncpg` and `ssl=`, and rejects `channel_binding`.
    The plain `postgresql://` URLs produced by the platform's new-product script go through here too.
    """
    parts = urlsplit(url)
    if parts.scheme not in ("postgres", "postgresql"):
        return url  # other schemes untouched (urlunsplit would also collapse sqlite:/// into sqlite:/)
    scheme = "postgresql+asyncpg"
    query = [
        ("ssl", val) if key == "sslmode" else (key, val)
        for key, val in parse_qsl(parts.query)
        if key != "channel_binding"
    ]
    return urlunsplit((scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
