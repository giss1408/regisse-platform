"""Moving-window rate limits (memory in tests, Redis otherwise). Ivorian carriers share IPs (CGNAT), so IP limits are loose
and sensitive limits are per phone."""

from limits import parse
from limits.storage import storage_from_string
from limits.strategies import MovingWindowRateLimiter

from app.config import get_settings

_limiter: MovingWindowRateLimiter | None = None


def limiter() -> MovingWindowRateLimiter:
    global _limiter
    if _limiter is None:
        _limiter = MovingWindowRateLimiter(storage_from_string(get_settings().rate_limit_storage))
    return _limiter


def reset() -> None:
    global _limiter
    _limiter = None


def item(spec: str):
    return parse(spec)
