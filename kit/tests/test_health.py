import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from regisse_kit.health import health_router


async def ok():
    return True


async def boom():
    raise RuntimeError("secret connection string must not leak")


async def get(app, path):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        return await c.get(path)


@pytest.mark.asyncio
async def test_healthz_is_always_ok_even_when_a_dependency_is_down():
    app = FastAPI()
    app.include_router(health_router({"db": boom}))
    r = await get(app, "/healthz")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_readyz_ok_and_degraded_without_leaking_details():
    good = FastAPI(); good.include_router(health_router({"db": ok, "redis": ok}))
    r = await get(good, "/readyz")
    assert r.status_code == 200 and r.json() == {"status": "ok", "checks": {"db": "ok", "redis": "ok"}}

    bad = FastAPI(); bad.include_router(health_router({"db": ok, "redis": boom}))
    r = await get(bad, "/readyz")
    assert r.status_code == 503
    assert r.json() == {"status": "degraded", "checks": {"db": "ok", "redis": "down"}}
    assert "secret" not in r.text


@pytest.mark.asyncio
async def test_readyz_without_checks_is_ok():
    app = FastAPI(); app.include_router(health_router())
    assert (await get(app, "/readyz")).status_code == 200
