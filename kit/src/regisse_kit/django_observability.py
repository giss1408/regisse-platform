"""Observability for Django products: request IDs, Prometheus metrics by route template, an access line without query strings, /metrics.

    MIDDLEWARE = ["regisse_kit.django_observability.ObservabilityMiddleware", ...]   # first
    OBSERVABILITY_SERVICE = "supermarket"
    METRICS_TOKEN = os.environ.get("METRICS_TOKEN", "")                              # empty = /metrics answers 404
    urlpatterns += [path("metrics", metrics_view)]                                  # never routed by Caddy (see infra/new-product.sh)

Same rules as the FastAPI module: route TEMPLATE labels (never the raw path), the query string is never logged (it carries phone numbers),
an incoming X-Request-ID is kept only when it is a short safe token. Attach `regisse_kit.redact.RedactingFilter` to your log handlers.
"""

import hmac
import logging
import re
import time
import uuid

from django.conf import settings
from django.http import Http404, HttpResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from ._metrics import HTTP_LATENCY, HTTP_REQUESTS, security_event  # noqa: F401  (security_event re-exported)

_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_SKIP = {"healthz", "readyz", "metrics"}


def _service() -> str:
    return getattr(settings, "OBSERVABILITY_SERVICE", "django")


class ObservabilityMiddleware:
    def __init__(self, get_response):  # type: ignore[no-untyped-def]
        self.get_response = get_response
        self.log = logging.getLogger(f"{_service()}.http")

    def __call__(self, request):  # type: ignore[no-untyped-def]
        rid = request.META.get("HTTP_X_REQUEST_ID", "")
        if not _REQUEST_ID.match(rid):
            rid = uuid.uuid4().hex
        request.request_id = rid
        start, status = time.perf_counter(), 500
        try:
            response = self.get_response(request)
            status = response.status_code
            response["X-Request-ID"] = rid
            return response
        finally:
            match = getattr(request, "resolver_match", None)
            route = (match.route if match is not None and match.route else None) or "unmatched"
            if route.strip("/") not in _SKIP:
                elapsed = time.perf_counter() - start
                HTTP_REQUESTS.labels(_service(), request.method, route, str(status)).inc()
                HTTP_LATENCY.labels(_service(), request.method, route).observe(elapsed)
                self.log.log(logging.ERROR if status >= 500 else logging.INFO, "request",
                             extra={"request_id": rid, "method": request.method, "route": route, "status": status,
                                    "duration_ms": round(elapsed * 1000, 1)})


def metrics_view(request):  # type: ignore[no-untyped-def]
    token = getattr(settings, "METRICS_TOKEN", "") or ""
    if not token:
        raise Http404
    given = request.META.get("HTTP_AUTHORIZATION", "").encode()
    if not hmac.compare_digest(given, f"Bearer {token}".encode()):
        return HttpResponse(status=401)
    return HttpResponse(generate_latest(), content_type=CONTENT_TYPE_LATEST)
