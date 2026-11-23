"""The platform's metric definitions (one registry entry each), shared by the FastAPI and Django adapters. Needs only prometheus_client."""

from prometheus_client import Counter, Histogram

HTTP_REQUESTS = Counter("http_requests_total", "HTTP requests", ["service", "method", "route", "status"])
HTTP_LATENCY = Histogram("http_request_duration_seconds", "HTTP request latency", ["service", "method", "route"],
                         buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10))
SECURITY_EVENTS = Counter("security_events_total", "Security-relevant events", ["service", "event"])


def security_event(service: str, event: str) -> None:
    """Counts a security-relevant event (login_failed, lockout, refresh_reuse, relay_sent, rate_limited...). Names are code constants, never user input."""
    SECURITY_EVENTS.labels(service=service, event=event).inc()
