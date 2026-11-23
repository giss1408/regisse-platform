"""Platform observability for FastAPI products: structured JSON logs, request IDs, Prometheus metrics, security-event counters.

    from regisse_kit.observability import install_observability, security_event
    install_observability(app, "auth", metrics_token=settings.metrics_token)
    security_event("auth", "login_failed")

Design rules (logs and metrics may be shipped to a managed service, so nothing sensitive may reach them):
- every log line is JSON and passes through `redact`: phone numbers, JWTs, Bearer tokens and values under secret-like keys never leave the process;
- metrics use the route TEMPLATE (`/items/{id}`), never the raw path, so user data and cardinality stay out of labels;
- `/metrics` exists only when a token is configured, needs `Authorization: Bearer <token>` (constant time) and must not be routed by Caddy;
- an incoming `X-Request-ID` is kept only if it is a short safe token (no log injection), otherwise a new one is generated.
"""

import hmac
import json
import logging
import re
import time
import uuid
from typing import Any

from .redact import REDACTED, redact, secret_key as _secret_key  # noqa: F401  (re-exported: redact lives in regisse_kit.redact)

try:
    from fastapi import FastAPI, Request, Response
    from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
except ImportError as exc:  # pragma: no cover
    raise ImportError("regisse_kit.observability needs the 'observability' extra: pip install regisse-kit[observability]") from exc

_STD_ATTRS = set(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {"message", "asctime"}
_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

HTTP_REQUESTS = Counter("http_requests_total", "HTTP requests", ["service", "method", "route", "status"])
HTTP_LATENCY = Histogram("http_request_duration_seconds", "HTTP request latency", ["service", "method", "route"],
                         buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10))
SECURITY_EVENTS = Counter("security_events_total", "Security-relevant events", ["service", "event"])


class JsonRedactingFormatter(logging.Formatter):
    def __init__(self, service: str) -> None:
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        doc: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "service": self.service,
            "logger": record.name,
            "msg": redact(record.getMessage()),
        }
        for k, v in record.__dict__.items():
            if k not in _STD_ATTRS and not k.startswith("_"):
                doc[k] = REDACTED if _secret_key(k) else redact(v)
        if record.exc_info:
            doc["exc"] = redact(self.formatException(record.exc_info))
        return json.dumps(doc, default=str)


def setup_logging(service: str, level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonRedactingFormatter(service))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers = []
        lg.propagate = True
    # uvicorn's access log carries raw paths (may hold identifiers); the middleware below logs the route template instead.
    logging.getLogger("uvicorn.access").disabled = True


def security_event(service: str, event: str) -> None:
    """Counts a security-relevant event (login_failed, lockout, refresh_reuse, relay_sent, rate_limited...). Names are code constants, never user input."""
    SECURITY_EVENTS.labels(service=service, event=event).inc()


def install_observability(app: FastAPI, service: str, *, metrics_token: str | None = None, log_level: str = "INFO") -> None:
    setup_logging(service, log_level)
    log = logging.getLogger(f"{service}.http")

    @app.middleware("http")
    async def observe(request: Request, call_next):  # type: ignore[no-untyped-def]
        rid = request.headers.get("x-request-id", "")
        if not _REQUEST_ID.match(rid):
            rid = uuid.uuid4().hex
        start = time.perf_counter()
        status = 500
        try:
            response: Response = await call_next(request)
            status = response.status_code
            response.headers["X-Request-ID"] = rid
            return response
        finally:
            route = getattr(request.scope.get("route"), "path", None) or "unmatched"
            if route != "/metrics":
                elapsed = time.perf_counter() - start
                HTTP_REQUESTS.labels(service, request.method, route, str(status)).inc()
                HTTP_LATENCY.labels(service, request.method, route).observe(elapsed)
                level = logging.ERROR if status >= 500 else logging.INFO
                log.log(level, "request", extra={"request_id": rid, "method": request.method, "route": route, "status": status,
                                                 "duration_ms": round(elapsed * 1000, 1)})

    if metrics_token:
        expected = f"Bearer {metrics_token}".encode()

        @app.get("/metrics", include_in_schema=False)
        async def metrics(request: Request) -> Response:
            given = request.headers.get("authorization", "").encode()
            if not hmac.compare_digest(given, expected):
                return Response(status_code=401)
            return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
