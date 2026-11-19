import time

import jwt
import pyotp

from app.config import get_settings
from tests.conftest import PASSWORD, admin_headers, make_user, register_and_activate

LOGIN = {"password": PASSWORD, "product": "flariane", "app": "customer"}


async def test_step_up_returns_a_fresh_token_for_the_same_session(client):
    boss = await admin_headers("super_admin", "0700000002")
    await register_and_activate(client, boss)
    t = (await client.post("/v1/login", json={"phone": "0701020304", **LOGIN})).json()
    old = jwt.decode(t["access_token"], options={"verify_signature": False})
    h = {"Authorization": f"Bearer {t['access_token']}"}
    time.sleep(1.1)
    r = await client.post("/v1/step-up", headers=h, json={"password": PASSWORD})
    assert r.status_code == 200 and r.json()["amr"] == ["pwd"]
    new = jwt.decode(r.json()["access_token"], options={"verify_signature": False})
    assert new["sid"] == old["sid"] and new["sub"] == old["sub"] and new["auth_time"] > old["auth_time"] and new["aud"] == old["aud"]
    assert (await client.get("/v1/me", headers={"Authorization": f"Bearer {r.json()['access_token']}"})).status_code == 200


async def test_step_up_needs_the_password_and_wrong_ones_lock(client):
    boss = await admin_headers("super_admin", "0700000002")
    await register_and_activate(client, boss)
    t = (await client.post("/v1/login", json={"phone": "0701020304", **LOGIN})).json()
    h = {"Authorization": f"Bearer {t['access_token']}"}
    assert (await client.post("/v1/step-up", json={"password": PASSWORD})).status_code == 401
    codes = [(await client.post("/v1/step-up", headers=h, json={"password": "wrong-password-1"})).status_code for _ in range(5)]
    assert codes == [401] * 5
    assert (await client.post("/v1/step-up", headers=h, json={"password": PASSWORD})).json()["detail"] == "login_locked"  # even the right one


async def test_a_session_that_ended_cannot_be_revived_by_a_step_up(client):
    boss = await admin_headers("super_admin", "0700000002")
    await register_and_activate(client, boss)
    t = (await client.post("/v1/login", json={"phone": "0701020304", **LOGIN})).json()
    h = {"Authorization": f"Bearer {t['access_token']}"}
    await client.post("/v1/logout", json={"refresh_token": t["refresh_token"]})
    r = await client.post("/v1/step-up", headers=h, json={"password": PASSWORD})
    assert r.status_code == 401 and r.json()["detail"] == "session_expired"


async def test_staff_must_also_prove_the_authenticator_code(client):
    secret = pyotp.random_base32()
    await make_user("0700000009", role="super_admin", product="auth", totp=secret)
    login = await client.post("/v1/login", json={"phone": "0700000009", "password": PASSWORD, "product": "auth", "app": "admin", "totp": pyotp.TOTP(secret).now()})
    h = {"Authorization": f"Bearer {login.json()['access_token']}"}
    assert (await client.post("/v1/step-up", headers=h, json={"password": PASSWORD})).json()["detail"] == "invalid_totp"
    # the login already used this 30 s step: a step-up needs the NEXT code (replay protection applies here too)
    nxt = pyotp.TOTP(secret).at(time.time() + 30)
    r = await client.post("/v1/step-up", headers=h, json={"password": PASSWORD, "totp": nxt})
    assert r.status_code == 200 and r.json()["amr"] == ["pwd", "totp"]
    assert (await client.post("/v1/step-up", headers=h, json={"password": PASSWORD, "totp": nxt})).json()["detail"] == "invalid_totp"  # a code works once
