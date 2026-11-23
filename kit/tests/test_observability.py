import json
import logging

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

from regisse_kit.observability import JsonRedactingFormatter, install_observability, redact, security_event


def fmt(msg: str, **extra) -> dict:
    rec = logging.LogRecord("t", logging.INFO, __file__, 1, msg, None, None)
    for k, v in extra.items():
        setattr(rec, k, v)
    return json.loads(JsonRedactingFormatter("svc").format(rec))


def test_redact_phone_jwt_bearer_and_secret_keys():
    assert "+2250701020304" not in redact("call +2250701020304 now")
    assert "0701020304" not in redact("tel 0701020304")
    jwt = "eyJhbGciOiJFZERTQSJ9.eyJzdWIiOiIxIn0.c2lnbmF0dXJl"
    assert jwt not in redact(f"token {jwt}")
    assert "abc123secret" not in redact("Authorization: Bearer abc123secret")
    assert redact({"password": "hunter2", "ok": "x"}) == {"password": "[redacted]", "ok": "x"}
    assert redact({"nested": {"code": "123456"}})["nested"]["code"] == "[redacted]"


def test_formatter_emits_json_with_service_and_redacts_extras():
    out = fmt("login for +2250701020304", user_code="123456", token="zzz")
    assert out["service"] == "svc" and out["level"] == "INFO"
    assert "+2250701020304" not in out["msg"]
    assert out["token"] == "[redacted]"


@pytest.fixture
def app():
    a = FastAPI()
    install_observability(a, "demo", metrics_token="s3cret")

    @a.get("/items/{item_id}")
    async def item(item_id: int):
        return {"id": item_id}

    @a.get("/boom")
    async def boom():
        raise HTTPException(status_code=500, detail="x")

    return a


async def get(app, path, **kw):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        return await c.get(path, **kw)


@pytest.mark.asyncio
async def test_request_id_generated_and_sanitised(app):
    r = await get(app, "/items/1")
    assert len(r.headers["x-request-id"]) >= 16
    r = await get(app, "/items/1", headers={"x-request-id": "abc-123"})
    assert r.headers["x-request-id"] == "abc-123"
    r = await get(app, "/items/1", headers={"x-request-id": "bad id\nforged line"})
    assert "\n" not in r.headers["x-request-id"] and r.headers["x-request-id"] != "bad id\nforged line"


@pytest.mark.asyncio
async def test_metrics_requires_token_and_uses_route_template_not_raw_path(app):
    await get(app, "/items/41")
    await get(app, "/items/42")
    await get(app, "/boom")
    assert (await get(app, "/metrics")).status_code == 401
    assert (await get(app, "/metrics", headers={"authorization": "Bearer wrong"})).status_code == 401
    r = await get(app, "/metrics", headers={"authorization": "Bearer s3cret"})
    assert r.status_code == 200
    body = r.text
    assert 'route="/items/{item_id}"' in body and "/items/41" not in body
    assert 'status="500"' in body and 'service="demo"' in body


@pytest.mark.asyncio
async def test_metrics_disabled_without_token():
    a = FastAPI()
    install_observability(a, "nometrics")
    assert (await get(a, "/metrics")).status_code == 404


@pytest.mark.asyncio
async def test_security_event_counter(app):
    security_event("demo", "login_failed")
    security_event("demo", "login_failed")
    r = await get(app, "/metrics", headers={"authorization": "Bearer s3cret"})
    assert 'security_events_total{event="login_failed",service="demo"} 2.0' in r.text
