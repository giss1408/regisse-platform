"""Platform health endpoints for FastAPI products: /healthz (liveness), /readyz (dependencies).

    from regisse_kit.health import health_router
    app.include_router(health_router({"db": check_db, "redis": check_redis}))

Checks are async callables returning True/raises. Caddy and container health checks use /healthz;
/readyz is for deploy gates and monitoring (503 when a dependency is down).
"""

from collections.abc import Awaitable, Callable, Mapping

try:
    from fastapi import APIRouter
    from fastapi.responses import JSONResponse
except ImportError as exc:  # pragma: no cover
    raise ImportError("regisse_kit.health needs the 'fastapi' extra: pip install regisse-kit[fastapi]") from exc

Check = Callable[[], Awaitable[object]]


def health_router(checks: Mapping[str, Check] | None = None) -> APIRouter:
    router = APIRouter()
    checks = dict(checks or {})

    @router.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @router.get("/readyz", include_in_schema=False)
    async def readyz() -> JSONResponse:
        results: dict[str, str] = {}
        for name, fn in checks.items():
            try:
                await fn()
                results[name] = "ok"
            except Exception as exc:  # noqa: BLE001: report any dependency failure, never leak details
                results[name] = "down"
        ok = all(v == "ok" for v in results.values())
        return JSONResponse({"status": "ok" if ok else "degraded", "checks": results}, status_code=200 if ok else 503)

    return router
