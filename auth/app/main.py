from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from regisse_kit.health import health_router
from regisse_kit.observability import install_observability
from sqlalchemy import text

from app import tokens
from app.config import get_settings
from app.db import SessionLocal, engine
from app.errors import install_error_handlers
from app.routers import admin, public


async def _check_db() -> None:
    async with engine.connect() as conn:
        await conn.execute(text("select 1"))


@asynccontextmanager
async def lifespan(_: FastAPI):
    async with SessionLocal() as db:
        await tokens.ensure_signing_key(db)
        await db.commit()
    yield


def create_app() -> FastAPI:
    s = get_settings()
    prod = s.env == "prod"
    app = FastAPI(title="regisse auth", version="0.1.0", lifespan=lifespan, docs_url=None if prod else "/docs", redoc_url=None, openapi_url=None if prod else "/openapi.json")

    @app.middleware("http")
    async def security_headers(request: Request, call_next):  # type: ignore[no-untyped-def]
        response: Response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Cache-Control", "no-store")
        if prod:
            response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
        return response

    install_observability(app, "auth", metrics_token=s.metrics_token or None)
    install_error_handlers(app)
    app.include_router(health_router({"db": _check_db}))
    app.include_router(public.router)
    app.include_router(admin.router)

    @app.get("/.well-known/jwks.json", include_in_schema=False)
    async def jwks_doc(response: Response) -> dict:
        async with SessionLocal() as db:
            doc = await tokens.jwks(db)
        response.headers["Cache-Control"] = "public, max-age=300"
        return doc

    return app


app = create_app()
