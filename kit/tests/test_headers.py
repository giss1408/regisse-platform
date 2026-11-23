import pytest
from fastapi import FastAPI, Response
from httpx import ASGITransport, AsyncClient

from regisse_kit.headers import install_security_headers


def make(prod: bool) -> FastAPI:
    app = FastAPI()
    install_security_headers(app, prod=prod)

    @app.get("/x")
    async def x():
        return {"ok": True}

    @app.get("/public")
    async def public(response: Response):
        response.headers["Cache-Control"] = "public, max-age=300"
        return {}

    return app


async def get(app, path):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        return await c.get(path)


@pytest.mark.asyncio
async def test_baseline_headers_and_hsts_only_in_prod():
    r = await get(make(False), "/x")
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["x-frame-options"] == "DENY"
    assert r.headers["cache-control"] == "no-store" and "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert "strict-transport-security" not in r.headers
    assert "max-age=63072000" in (await get(make(True), "/x")).headers["strict-transport-security"]


@pytest.mark.asyncio
async def test_a_route_may_choose_its_own_cache_control():
    assert (await get(make(True), "/public")).headers["cache-control"] == "public, max-age=300"


@pytest.mark.asyncio
async def test_headers_also_on_404():
    assert (await get(make(True), "/nope")).headers["x-content-type-options"] == "nosniff"
