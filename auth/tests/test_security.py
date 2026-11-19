"""Findings of the security review of auth (each test pins one fix). See next_steps.md, "auth security review"."""

import asyncio
import uuid
from datetime import timedelta

import jwt
import pyotp
from sqlalchemy import delete, select, update

from app import tokens
from app.config import get_settings
from app.db import SessionLocal
from app.models import CodeChallenge, Membership, RefreshToken, User
from app.services import accounts
from tests.conftest import PASSWORD, admin_headers, code_from_url, make_user, register_and_activate

LOGIN = {"password": PASSWORD, "product": "flariane", "app": "customer"}


async def login(client, phone="0701020304", **over):
    return await client.post("/v1/login", json={"phone": phone, **LOGIN, **over})


# --- F1: an admin token is not enough, the database decides --------------------------------------------------------------


async def test_a_demoted_admin_loses_admin_power_at_once_not_at_token_expiry(client):
    h = await admin_headers("admin", "0700000001")
    assert (await client.get("/v1/admin/relay-queue", headers=h)).status_code == 200
    async with SessionLocal() as db:
        await db.execute(delete(Membership).where(Membership.product == "auth", Membership.role == "admin"))
        await db.commit()
    assert (await client.get("/v1/admin/relay-queue", headers=h)).status_code == 403


async def test_a_disabled_user_cannot_act_with_a_token_that_is_still_valid(client):
    boss = await admin_headers("super_admin", "0700000002")
    await register_and_activate(client, boss)
    t = (await login(client)).json()
    h = {"Authorization": f"Bearer {t['access_token']}"}
    assert (await client.get("/v1/me", headers=h)).status_code == 200
    assert (await client.post(f"/v1/admin/users/{t['user_id']}/disable", headers=boss)).status_code == 204
    assert (await client.get("/v1/me", headers=h)).status_code == 401
    assert (await client.post("/v1/password/change", headers=h, json={"current_password": PASSWORD, "new_password": "Brand-New-Pass-1"})).status_code == 401
    assert (await client.post("/v1/me/delete", headers=h, json={"current_password": PASSWORD})).status_code == 401
    # a disabled ADMIN token is refused on admin endpoints too
    async with SessionLocal() as db:
        await db.execute(update(User).where(User.display_name == "Admin").values(status="disabled"))
        await db.commit()
    assert (await client.get("/v1/admin/relay-queue", headers=boss)).status_code == 401


# --- F2: a service key only speaks for its own product ------------------------------------------------------------------


async def test_service_keys_are_bound_to_their_product(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "service_keys_json", '{"flariane": "svc-flariane-key", "fidelia": "svc-fidelia-key"}')
    boss = await admin_headers("super_admin", "0700000002")
    await register_and_activate(client, boss)
    t = (await login(client)).json()  # a flariane token
    body = {"token": t["access_token"]}
    own = await client.post("/v1/introspect", json=body, headers={"X-Service-Key": "svc-flariane-key"})
    other = await client.post("/v1/introspect", json=body, headers={"X-Service-Key": "svc-fidelia-key"})
    assert own.json()["active"] is True
    assert other.status_code == 200 and other.json() == {"active": False}  # fidelia's key says nothing about flariane tokens
    sid = jwt.decode(t["access_token"], options={"verify_signature": False})["sid"]
    assert (await client.post("/v1/sessions/check", json={"sid": sid}, headers={"X-Service-Key": "svc-flariane-key"})).json() == {"active": True}
    assert (await client.post("/v1/sessions/check", json={"sid": sid}, headers={"X-Service-Key": "svc-fidelia-key"})).json() == {"active": False}
    assert (await client.post("/v1/sessions/check", json={"sid": sid}, headers={"X-Service-Key": "svc-flariane-keyX"})).status_code == 403


# --- F3: races ---------------------------------------------------------------------------------------------------------------


async def test_one_refresh_token_cannot_be_used_twice_in_parallel(client):
    boss = await admin_headers("super_admin", "0700000002")
    await register_and_activate(client, boss)
    t = (await login(client)).json()
    results = await asyncio.gather(*[client.post("/v1/refresh", json={"refresh_token": t["refresh_token"]}) for _ in range(4)])
    assert sorted(r.status_code for r in results).count(200) == 1  # exactly one wins; the replays end the session
    assert all(r.status_code in (200, 401) for r in results)


async def test_one_authenticator_code_cannot_open_two_sessions_in_parallel(client):
    secret = pyotp.random_base32()
    await make_user("0700000009", role="super_admin", product="auth", totp=secret)
    body = {"phone": "0700000009", "password": PASSWORD, "product": "auth", "app": "admin", "totp": pyotp.TOTP(secret).now()}
    results = await asyncio.gather(*[client.post("/v1/login", json=body) for _ in range(4)])
    assert [r.status_code for r in results].count(200) == 1


async def test_parallel_guesses_cannot_exceed_the_attempt_limit(client):
    boss = await admin_headers("super_admin", "0700000002")
    await client.post("/v1/register", json={"phone": "0701020304", "password": PASSWORD, "product": "flariane", "consent": True})
    item = (await client.get("/v1/admin/relay-queue", headers=boss)).json()[0]
    code = code_from_url((await client.post(f"/v1/admin/relay/{item['id']}/send", headers=boss)).json()["url"])
    bad = "000000" if code != "000000" else "111111"
    await asyncio.gather(*[client.post("/v1/activate", json={"phone": "0701020304", "code": bad}) for _ in range(12)])
    async with SessionLocal() as db:
        ch = (await db.scalars(select(CodeChallenge))).one()
    assert ch.attempts == 5  # twelve parallel guesses, five counted, the rest refused


async def test_two_signups_with_the_same_number_at_once_give_201_and_409_never_500(client):
    body = {"phone": "0701020304", "password": PASSWORD, "product": "flariane", "consent": True}
    results = await asyncio.gather(*[client.post("/v1/register", json=body) for _ in range(3)])
    assert sorted(r.status_code for r in results) == [201, 409, 409]


# --- F4: limits per IP ---------------------------------------------------------------------------------------------------


async def test_one_ip_cannot_try_many_accounts(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "login_ip_limit", "5/15minute")
    codes = [(await login(client, phone=f"07000000{i:02d}", password="wrong-password-1")).status_code for i in range(7)]
    assert codes[:5] == [401] * 5 and codes[5:] == [429, 429]


async def test_activation_and_refresh_are_limited_per_ip(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "activate_ip_limit", "3/15minute")
    monkeypatch.setattr(get_settings(), "refresh_ip_limit", "3/minute")
    a = [(await client.post("/v1/activate", json={"phone": f"07010203{i:02d}", "code": "123456"})).status_code for i in range(5)]
    assert a[:3] == [400] * 3 and a[3:] == [429, 429]
    r = [(await client.post("/v1/refresh", json={"refresh_token": "x" * 30})).status_code for _ in range(5)]
    assert r[:3] == [401] * 3 and r[3:] == [429, 429]


# --- F5: no oracle on activation -----------------------------------------------------------------------------------------


async def test_activation_answers_the_same_for_active_unknown_and_disabled_numbers(client):
    boss = await admin_headers("super_admin", "0700000002")
    await register_and_activate(client, boss, "0701020304")
    await make_user("0702020202", role="customer", status="disabled")
    answers = [await client.post("/v1/activate", json={"phone": p, "code": "123456"}) for p in ("0701020304", "0799999999", "0702020202")]
    assert {(r.status_code, r.json()["detail"]) for r in answers} == {(400, "wrong_activation_code")}


# --- F7: nothing the user typed is echoed back ----------------------------------------------------------------------------


async def test_validation_errors_never_echo_what_was_sent(client):
    r = await client.post("/v1/login", json={"phone": "0701020304", "password": "", "product": "flariane", "app": "customer", "totp": "SECRET-ish"})
    assert r.status_code == 422 and r.json()["detail"] == "validation_error"
    assert "SECRET-ish" not in r.text and "0701020304" not in r.text and "input" not in r.json()


# --- F8/F10: hygiene -------------------------------------------------------------------------------------------------------


async def test_display_names_cannot_inject_lines_or_formatting_into_whatsapp_messages(client):
    boss = await admin_headers("super_admin", "0700000002")
    r = await client.post("/v1/register", json={"phone": "0701020304", "password": PASSWORD, "display_name": "Awa\n*FAKE* _x_ <b>http://evil</b>", "product": "flariane", "consent": True})
    assert r.status_code == 201
    item = (await client.get("/v1/admin/relay-queue", headers=boss)).json()[0]
    url = (await client.post(f"/v1/admin/relay/{item['id']}/send", headers=boss)).json()["url"]
    from urllib.parse import parse_qs, urlsplit
    text = parse_qs(urlsplit(url).query)["text"][0]
    assert "FAKE" in text and "\n*FAKE*" not in text and "<b>" not in text and "_x_" not in text
    assert text.splitlines()[0].startswith("Bonjour Awa")


async def test_common_passwords_and_the_phone_number_are_refused(client):
    base = {"phone": "0701020304", "display_name": "A", "product": "flariane", "consent": True}
    for pw in ("password", "Motdepasse1", "12345678", "0701020304", "+225 0701020304"):
        r = await client.post("/v1/register", json={**base, "password": pw})
        assert r.status_code == 400 and r.json()["detail"] == "weak_password", pw


async def test_reset_is_only_for_users_of_that_product(client):
    boss = await admin_headers("super_admin", "0700000002")
    await register_and_activate(client, boss, "0701020304", "flariane")
    r = await client.post("/v1/password/reset-request", json={"phone": "0701020304", "product": "fidelia"})
    assert r.status_code == 202
    assert [i for i in (await client.get("/v1/admin/relay-queue", headers=boss)).json() if i["purpose"] == "password_reset"] == []
