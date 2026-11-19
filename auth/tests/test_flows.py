import time

import jwt
import pyotp
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from sqlalchemy import select

from app import tokens
from app.db import SessionLocal
from app.models import AuditLog, RefreshToken
from tests.conftest import PASSWORD, admin_headers, code_from_url, make_user, register_and_activate, temp_from_url

LOGIN = {"password": PASSWORD, "product": "flariane", "app": "customer"}


async def login(client, phone="0701020304", **over):
    return await client.post("/v1/login", json={"phone": phone, **LOGIN, **over})


# --- registration, relay, activation -------------------------------------------------------------


async def test_register_goes_to_relay_queue_and_login_is_blocked_until_activation(client):
    admin = await admin_headers()
    r = await client.post("/v1/register", json={"phone": "07 01 02 03 04", "password": PASSWORD, "display_name": "Fatou Test", "product": "flariane", "consent": True})
    assert r.status_code == 201 and r.json()["status"] == "pending_activation"
    q = (await client.get("/v1/admin/relay-queue", headers=admin)).json()
    assert len(q) == 1 and q[0]["purpose"] == "activation" and q[0]["phone_masked"] == "+225 07 •• •• 04"
    assert "0701020304" not in str(q)  # never the full number in the queue
    r = await login(client)
    assert r.status_code == 403 and r.json()["detail"] == "not_activated"


async def test_registration_rules(client):
    base = {"phone": "0701020304", "password": PASSWORD, "display_name": "F", "product": "flariane", "consent": True}
    assert (await client.post("/v1/register", json={**base, "consent": False})).json()["detail"] == "consent_required"
    assert (await client.post("/v1/register", json={**base, "product": "auth"})).json()["detail"] == "registration_closed"
    assert (await client.post("/v1/register", json={**base, "product": "nope"})).json()["detail"] == "unknown_product"
    assert (await client.post("/v1/register", json={**base, "phone": "12345678"})).json()["detail"] == "invalid_phone"
    assert (await client.post("/v1/register", json={**base, "password": "11111111"})).json()["detail"] == "weak_password"
    assert (await client.post("/v1/register", json=base)).status_code == 201
    assert (await client.post("/v1/register", json=base)).json()["detail"] == "phone_taken"


async def test_activation_code_attempts_expiry_and_single_use(client):
    admin = await admin_headers()
    await client.post("/v1/register", json={"phone": "0701020304", "password": PASSWORD, "product": "flariane", "consent": True})
    item = (await client.get("/v1/admin/relay-queue", headers=admin)).json()[0]
    code = code_from_url((await client.post(f"/v1/admin/relay/{item['id']}/send", headers=admin)).json()["url"])
    bad = "000000" if code != "000000" else "111111"
    for _ in range(5):
        assert (await client.post("/v1/activate", json={"phone": "0701020304", "code": bad})).json()["detail"] == "wrong_activation_code"
    r = await client.post("/v1/activate", json={"phone": "0701020304", "code": code})
    assert r.status_code == 429 and r.json()["detail"] == "activation_locked"  # locked even for the right code
    # a new code from the admin voids the old one and resets attempts
    code2 = code_from_url((await client.post(f"/v1/admin/relay/{item['id']}/send", headers=admin)).json()["url"])
    assert (await client.post("/v1/activate", json={"phone": "0701020304", "code": code2})).status_code == 200
    # single use: the code does nothing afterwards, and the request is closed
    assert (await client.post(f"/v1/admin/relay/{item['id']}/send", headers=admin)).status_code == 409
    assert (await login(client)).status_code == 200


async def test_reject_releases_the_number(client):
    admin = await admin_headers()
    await client.post("/v1/register", json={"phone": "0701020304", "password": PASSWORD, "product": "flariane", "consent": True})
    item = (await client.get("/v1/admin/relay-queue", headers=admin)).json()[0]
    assert (await client.post(f"/v1/admin/relay/{item['id']}/reject", headers=admin)).status_code == 204
    assert (await client.post("/v1/register", json={"phone": "0701020304", "password": PASSWORD, "product": "flariane", "consent": True})).status_code == 201


# --- tokens ------------------------------------------------------------------------------------


async def test_login_issues_eddsa_token_verifiable_with_jwks(client):
    admin = await admin_headers()
    await register_and_activate(client, admin)
    r = await login(client)
    assert r.status_code == 200
    body = r.json()
    jwks = (await client.get("/.well-known/jwks.json")).json()
    header = jwt.get_unverified_header(body["access_token"])
    assert header["alg"] == "EdDSA"
    jwk = next(k for k in jwks["keys"] if k["kid"] == header["kid"])
    pub = Ed25519PublicKey.from_public_bytes(tokens._unb64u(jwk["x"]))
    claims = jwt.decode(body["access_token"], pub, algorithms=["EdDSA"], audience="flariane", issuer="http://auth.test")
    assert claims["role"] == "customer" and claims["roles"] == ["customer"] and claims["app"] == "customer"
    assert claims["ph"] == "tel:+2250701020304" and claims["amr"] == ["pwd"] and claims["sid"]
    assert claims["exp"] - claims["iat"] == 600 and abs(claims["auth_time"] - time.time()) < 5
    assert len(claims["sub"]) == 36  # the hidden global id (uuid)
    assert "0701020304" not in body["access_token"]  # no raw phone in the token payload
    me = await client.get("/v1/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.json()["phone_masked"] == "+225 07 •• •• 04"


async def test_forged_and_wrong_algorithm_tokens_are_refused(client):
    admin = await admin_headers()
    await register_and_activate(client, admin)
    good = (await login(client)).json()["access_token"]
    header = jwt.get_unverified_header(good)
    payload = jwt.decode(good, options={"verify_signature": False})
    forged_hs = jwt.encode(payload, "secret" * 8, algorithm="HS256", headers={"kid": header["kid"]})
    forged_none = jwt.encode(payload, None, algorithm="none", headers={"kid": header["kid"]})
    tampered = good[:-4] + ("AAAA" if not good.endswith("AAAA") else "BBBB")
    for tok in (forged_hs, forged_none, tampered, "garbage"):
        r = await client.get("/v1/me", headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code == 401, tok[:20]
    assert (await client.get("/v1/me")).status_code == 401


async def test_login_errors_do_not_reveal_which_numbers_have_accounts(client):
    admin = await admin_headers()
    await register_and_activate(client, admin)
    a = await login(client, password="wrong-password-1")
    b = await login(client, phone="0705050505")
    assert a.status_code == b.status_code == 401 and a.json() == b.json() == {"detail": "invalid_credentials"}


async def test_login_locks_after_repeated_failures(client):
    admin = await admin_headers()
    await register_and_activate(client, admin)
    for _ in range(5):
        assert (await login(client, password="wrong-password-1")).status_code == 401
    r = await login(client)  # even the right password
    assert r.status_code == 429 and r.json()["detail"] == "login_locked"


async def test_no_access_to_an_app_without_a_membership(client):
    admin = await admin_headers()
    await register_and_activate(client, admin)
    r = await login(client, app="admin")
    assert r.status_code == 403 and r.json()["detail"] == "no_access_to_app"
    assert (await login(client, product="fidelia", app="customer")).json()["detail"] == "no_access_to_app"


# --- sessions ----------------------------------------------------------------------------------


async def test_refresh_rotates_and_reuse_revokes_the_whole_session(client):
    admin = await admin_headers()
    await register_and_activate(client, admin)
    first = (await login(client)).json()
    second = (await client.post("/v1/refresh", json={"refresh_token": first["refresh_token"]})).json()
    assert second["refresh_token"] != first["refresh_token"]
    # the old token is copied/stolen: using it again ends the session on every copy
    assert (await client.post("/v1/refresh", json={"refresh_token": first["refresh_token"]})).status_code == 401
    assert (await client.post("/v1/refresh", json={"refresh_token": second["refresh_token"]})).status_code == 401


async def test_logout_revokes_the_session(client):
    admin = await admin_headers()
    await register_and_activate(client, admin)
    t = (await login(client)).json()
    assert (await client.post("/v1/logout", json={"refresh_token": t["refresh_token"]})).status_code == 204
    assert (await client.post("/v1/refresh", json={"refresh_token": t["refresh_token"]})).status_code == 401
    assert (await client.post("/v1/logout", json={"refresh_token": "x" * 30})).status_code == 204  # idempotent


async def test_refresh_tokens_are_stored_hashed(client):
    admin = await admin_headers()
    await register_and_activate(client, admin)
    t = (await login(client)).json()
    async with SessionLocal() as db:
        rows = (await db.scalars(select(RefreshToken))).all()
    assert rows and all(r.token_hash != t["refresh_token"] and len(r.token_hash) == 64 for r in rows)


# --- password reset (relay) --------------------------------------------------------------------


async def test_password_reset_flow_with_temp_password(client):
    admin = await admin_headers()
    await register_and_activate(client, admin)
    old = (await login(client)).json()
    r = await client.post("/v1/password/reset-request", json={"phone": "0701020304", "product": "flariane"})
    r2 = await client.post("/v1/password/reset-request", json={"phone": "0799999999", "product": "flariane"})
    assert r.status_code == r2.status_code == 202 and r.json() == r2.json()  # same answer for unknown numbers
    q = (await client.get("/v1/admin/relay-queue", headers=admin)).json()
    assert [i["purpose"] for i in q] == ["password_reset"]
    temp = temp_from_url((await client.post(f"/v1/admin/relay/{q[0]['id']}/send", headers=admin)).json()["url"])
    assert (await login(client)).status_code == 401  # old password gone
    assert (await client.post("/v1/refresh", json={"refresh_token": old["refresh_token"]})).status_code == 401  # old sessions revoked
    t = await login(client, password=temp)
    assert t.status_code == 200 and t.json()["must_change_password"] is True
    claims = jwt.decode(t.json()["access_token"], options={"verify_signature": False})
    assert claims["mcp"] is True
    h = {"Authorization": f"Bearer {t.json()['access_token']}"}
    assert (await client.post("/v1/password/change", headers=h, json={"current_password": temp, "new_password": "Brand-New-Pass-1"})).status_code == 204
    assert (await login(client, password=temp)).status_code == 401
    assert (await login(client, password="Brand-New-Pass-1")).json()["must_change_password"] is False


async def test_privileged_accounts_never_use_the_relay(client):
    admin = await admin_headers()
    await make_user("0702020202", role="operator", product="flariane", totp=pyotp.random_base32())
    await client.post("/v1/password/reset-request", json={"phone": "0702020202", "product": "flariane"})
    assert (await client.get("/v1/admin/relay-queue", headers=admin)).json() == []  # nothing was queued


async def test_reset_requests_are_rate_limited(client):
    admin = await admin_headers()
    await register_and_activate(client, admin)
    codes = [(await client.post("/v1/password/reset-request", json={"phone": "0701020304", "product": "flariane"})).status_code for _ in range(4)]
    assert codes == [202, 202, 202, 429]
    open_resets = [i for i in (await client.get("/v1/admin/relay-queue", headers=admin)).json() if i["purpose"] == "password_reset"]
    assert len(open_resets) == 1  # repeated requests do not pile up in the queue


# --- admin and staff ---------------------------------------------------------------------------


async def test_admin_endpoints_need_an_admin_session_with_totp(client):
    admin = await admin_headers()
    await register_and_activate(client, admin)
    cust = {"Authorization": f"Bearer {(await login(client)).json()['access_token']}"}
    assert (await client.get("/v1/admin/relay-queue")).status_code == 401
    assert (await client.get("/v1/admin/relay-queue", headers=cust)).status_code == 403
    # an admin token without totp in amr is refused
    u = await make_user("0703030303", role="admin", product="auth", totp=pyotp.random_base32())
    async with SessionLocal() as db:
        import datetime, uuid
        tok = await tokens.issue_access_token(db, sub=u.id, phone_key="tel:x", product="auth", app="admin", role="admin", roles=["admin"], amr=["pwd"],
                                              auth_time=tokens.utcnow(), sid=uuid.uuid4().hex, must_change_password=False, ttl=datetime.timedelta(minutes=5))
    assert (await client.get("/v1/admin/relay-queue", headers={"Authorization": f"Bearer {tok}"})).status_code == 403


async def test_admin_login_requires_totp_and_blocks_replay(client):
    secret = pyotp.random_base32()
    await make_user("0700000009", role="super_admin", product="auth", totp=secret)
    body = {"phone": "0700000009", "password": PASSWORD, "product": "auth", "app": "admin"}
    assert (await client.post("/v1/login", json=body)).json()["detail"] == "invalid_totp"
    assert (await client.post("/v1/login", json={**body, "totp": "000000"})).json()["detail"] == "invalid_totp"
    code = pyotp.TOTP(secret).now()
    ok = await client.post("/v1/login", json={**body, "totp": code})
    assert ok.status_code == 200 and jwt.decode(ok.json()["access_token"], options={"verify_signature": False})["amr"] == ["pwd", "totp"]
    replay = await client.post("/v1/login", json={**body, "totp": code})
    assert replay.status_code == 401 and replay.json()["detail"] == "invalid_totp"  # a used code cannot be replayed


async def test_staff_roles_need_a_super_admin(client):
    admin = await admin_headers("admin", "0700000001")
    boss = await admin_headers("super_admin", "0700000002")
    cust = await make_user("0701010101", role="customer")
    body = {"user_id": str(cust.id), "product": "flariane", "role": "operator"}
    assert (await client.post("/v1/admin/memberships", headers=admin, json=body)).json()["detail"] == "super_admin_only"
    assert (await client.post("/v1/admin/memberships", headers=boss, json=body)).status_code == 201
    assert (await client.post("/v1/admin/memberships", headers=boss, json=body)).json()["detail"] == "already_member"
    harmless = {"user_id": str(cust.id), "product": "fidelia", "role": "customer"}
    assert (await client.post("/v1/admin/memberships", headers=admin, json=harmless)).status_code == 201
    assert (await client.post("/v1/admin/memberships", headers=boss, json={**body, "role": "emperor"})).json()["detail"] == "unknown_role"
    # an admin cannot disable another admin or a super admin; a super admin can disable an admin
    other = await make_user("0700000003", role="admin", product="auth", totp=pyotp.random_base32())
    assert (await client.post(f"/v1/admin/users/{other.id}/disable", headers=admin)).json()["detail"] == "super_admin_only"
    assert (await client.post(f"/v1/admin/users/{other.id}/disable", headers=boss)).status_code == 204


async def test_disabling_a_user_ends_access_and_sessions(client):
    admin = await admin_headers()
    await register_and_activate(client, admin)
    t = (await login(client)).json()
    uid = t["user_id"]
    assert (await client.post(f"/v1/admin/users/{uid}/disable", headers=admin)).status_code == 204
    assert (await login(client)).json()["detail"] == "account_blocked"
    assert (await client.post("/v1/refresh", json={"refresh_token": t["refresh_token"]})).status_code == 401
    assert (await client.get("/v1/me", headers={"Authorization": f"Bearer {t['access_token']}"})).status_code == 401  # /me checks status
    assert (await client.post(f"/v1/admin/users/{uid}/enable", headers=admin)).status_code == 204
    assert (await login(client)).status_code == 200


# --- introspection, jwks, audit ------------------------------------------------------------------


async def test_introspect_needs_a_service_key_and_sees_revocation(client):
    admin = await admin_headers()
    await register_and_activate(client, admin)
    t = (await login(client)).json()
    body = {"token": t["access_token"]}
    assert (await client.post("/v1/introspect", json=body)).status_code == 403
    assert (await client.post("/v1/introspect", json=body, headers={"X-Service-Key": "wrong"})).status_code == 403
    h = {"X-Service-Key": "svc-flariane-key"}
    r = (await client.post("/v1/introspect", json=body, headers=h)).json()
    assert r["active"] is True and r["aud"] == "flariane" and r["role"] == "customer"
    await client.post("/v1/logout", json={"refresh_token": t["refresh_token"]})
    assert (await client.post("/v1/introspect", json=body, headers=h)).json() == {"active": False}  # the signature is still valid, the session is not


async def test_key_rotation_keeps_old_tokens_verifiable(client):
    admin = await admin_headers()
    await register_and_activate(client, admin)
    old_token = (await login(client)).json()["access_token"]
    async with SessionLocal() as db:
        await tokens.rotate_key(db)
        await db.commit()
    jwks = (await client.get("/.well-known/jwks.json")).json()
    assert len(jwks["keys"]) == 2
    assert (await client.get("/v1/me", headers={"Authorization": f"Bearer {old_token}"})).status_code == 200
    new_token = (await login(client)).json()["access_token"]
    assert jwt.get_unverified_header(new_token)["kid"] != jwt.get_unverified_header(old_token)["kid"]


async def test_audit_log_has_no_secrets_or_full_phone_numbers(client):
    admin = await admin_headers()
    await register_and_activate(client, admin)
    await login(client)
    async with SessionLocal() as db:
        rows = (await db.scalars(select(AuditLog))).all()
    actions = {r.action for r in rows}
    assert {"user.registered", "relay.activation.sent", "user.activated", "user.login"} <= actions
    dump = str([(r.detail, r.entity_id, r.ip) for r in rows])
    assert "0701020304" not in dump and PASSWORD not in dump


async def test_health_and_headers(client):
    r = await client.get("/healthz")
    assert r.status_code == 200 and r.headers["x-content-type-options"] == "nosniff" and r.headers["cache-control"] == "no-store"
    assert (await client.get("/readyz")).json()["checks"] == {"db": "ok"}
