"""Monitoring: /metrics is private, counts security events, and never carries user data."""

import logging

from tests.conftest import PASSWORD, admin_headers, make_user, register_and_activate

METRICS = {"Authorization": "Bearer metrics-test-token"}


async def test_metrics_needs_the_token(client):
    assert (await client.get("/metrics")).status_code == 401
    assert (await client.get("/metrics", headers={"Authorization": "Bearer nope"})).status_code == 401
    assert (await client.get("/metrics", headers=METRICS)).status_code == 200


async def test_failed_and_successful_logins_are_counted_without_phone_numbers(client):
    await make_user("0701020304", role="customer")
    body = {"phone": "0701020304", "product": "flariane", "app": "customer"}
    await client.post("/v1/login", json={**body, "password": "Wrong-Password-1"})
    await client.post("/v1/login", json={**body, "password": PASSWORD})
    text = (await client.get("/metrics", headers=METRICS)).text
    assert 'security_events_total{event="401_invalid_credentials",service="auth"}' in text
    assert 'security_events_total{event="login_ok",service="auth"}' in text
    assert "0701020304" not in text and "701020304" not in text


async def test_relay_sends_are_counted(client):
    admin = await admin_headers()
    await register_and_activate(client, admin)
    text = (await client.get("/metrics", headers=METRICS)).text
    assert 'security_events_total{event="relay_sent",service="auth"}' in text


async def test_request_log_has_template_and_request_id_but_no_phone_or_code(client, caplog):
    caplog.set_level(logging.INFO)
    r = await client.post("/v1/login", json={"phone": "0701020304", "password": PASSWORD, "product": "flariane", "app": "customer"},
                          headers={"X-Request-ID": "req-test-1"})
    assert r.headers["x-request-id"] == "req-test-1"
    lines = [rec for rec in caplog.records if rec.name == "auth.http"]
    assert lines and lines[-1].request_id == "req-test-1" and lines[-1].route == "/v1/login"
    assert "0701020304" not in " ".join(str(v) for rec in lines for v in rec.__dict__.values())
