"""Security headers for API products: one place, same answer everywhere.

    from regisse_kit.headers import install_security_headers
    install_security_headers(app, prod=settings.env == "prod")

Headers are set with `setdefault` where a route may legitimately choose its own (Cache-Control for a public JWKS, for example).
"""

from fastapi import FastAPI, Request, Response

_BASE = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Cache-Control": "no-store",
    "Cross-Origin-Resource-Policy": "same-site",
    "Permissions-Policy": "geolocation=(), camera=(), microphone=(), payment=()",
    # An API serves JSON: nothing may be loaded or framed from its answers.
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
}
_HSTS = "max-age=63072000; includeSubDomains"


def install_security_headers(app: FastAPI, *, prod: bool) -> None:
    @app.middleware("http")
    async def security_headers(request: Request, call_next):  # type: ignore[no-untyped-def]
        response: Response = await call_next(request)
        for k, v in _BASE.items():
            if k == "Content-Security-Policy" and request.url.path == "/docs":
                continue  # Swagger UI (served only outside prod) loads its own scripts
            response.headers.setdefault(k, v)
        response.headers["X-Content-Type-Options"] = "nosniff"
        if prod:
            response.headers["Strict-Transport-Security"] = _HSTS
        return response
