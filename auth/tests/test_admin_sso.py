"""Back-office users: login by email, flariane admins imported with their authenticator, session check for the product."""

import jwt
import pyotp
from regisse_kit.crypto import hash_password
from sqlalchemy import select

from app.db import SessionLocal
from app.models import LegacyIdentity, User
from app.services import accounts
from app.services.legacy_import import import_admin_rows
from tests.conftest import PASSWORD, admin_headers

SECRET = pyotp.random_base32()


def arow(i, email="awa@flariane.ci", role="OPERATOR", **over):
    return {"id": i, "email": email, "name": "Awa Admin", "password_hash": hash_password(PASSWORD), "totp_secret": SECRET, "totp_required": True, "role": role, "active": True, **over}


async def run(rows, apply=True, refresh=False):
    async with SessionLocal() as db:
        stats = await import_admin_rows(db, rows, product="flariane", apply=apply, refresh=refresh)
        await db.commit()
    return stats


def body(email="awa@flariane.ci", **over):
    return {"email": email, "password": PASSWORD, "product": "flariane", "app": "admin", "totp": pyotp.TOTP(SECRET).now(), **over}


async def test_imported_admin_logs_in_by_email_with_her_existing_authenticator_and_carries_her_flariane_id(client):
    stats = await run([arow(5)])
    assert stats["created"] == 1 and stats["errors"] == []
    r = await client.post("/v1/login", json=body())
    assert r.status_code == 200, r.text
    claims = jwt.decode(r.json()["access_token"], options={"verify_signature": False})
    assert claims["role"] == "operator" and claims["amr"] == ["pwd", "totp"] and claims["aud"] == "flariane" and claims["app"] == "admin"
    assert claims["lid"] == "5" and claims["ph"] == ""  # her flariane AdminUser id; no phone for a back-office user
    assert "awa@flariane.ci" not in r.json()["access_token"]


async def test_email_login_needs_the_code_and_gives_nothing_away(client):
    await run([arow(5)])
    assert (await client.post("/v1/login", json=body(totp=None))).json()["detail"] == "invalid_totp"
    a = await client.post("/v1/login", json=body(password="wrong-password-1"))
    b = await client.post("/v1/login", json=body(email="nobody@flariane.ci"))
    assert a.status_code == b.status_code == 401 and a.json() == b.json() == {"detail": "invalid_credentials"}
    assert (await client.post("/v1/login", json={"password": PASSWORD, "product": "flariane", "app": "admin"})).json()["detail"] == "phone_or_email"
    assert (await client.post("/v1/login", json={**body(), "phone": "0701020304"})).json()["detail"] == "phone_or_email"


async def test_email_login_locks_after_repeated_failures(client):
    await run([arow(5)])
    for _ in range(5):
        assert (await client.post("/v1/login", json=body(password="wrong-password-1"))).status_code == 401
    assert (await client.post("/v1/login", json=body())).json()["detail"] == "login_locked"


async def test_a_totp_exempt_admin_keeps_working_without_a_code_but_never_a_super_admin(client):
    await run([arow(6, "pick@flariane.ci", "PICKUP", totp_required=False), arow(7, "boss@flariane.ci", "SUPER_ADMIN", totp_required=False)])
    ok = await client.post("/v1/login", json={"email": "pick@flariane.ci", "password": PASSWORD, "product": "flariane", "app": "admin"})
    assert ok.status_code == 200 and jwt.decode(ok.json()["access_token"], options={"verify_signature": False})["amr"] == ["pwd"]
    boss = await client.post("/v1/login", json={"email": "boss@flariane.ci", "password": PASSWORD, "product": "flariane", "app": "admin"})
    assert boss.json()["detail"] == "invalid_totp"  # flariane forces a code for super admins too


async def test_import_rules_idempotent_dry_run_errors_and_secrets_not_in_stats(client):
    rows = [arow(1), arow(2, "two@flariane.ci", "VIEWER"), arow(3, "not-an-email"), arow(4, "four@flariane.ci", "EMPEROR"), arow(5, "five@flariane.ci", totp_secret=None)]
    dry = await run(rows, apply=False)
    assert dry["created"] == 2 and len(dry["errors"]) == 3
    async with SessionLocal() as db:
        assert (await db.scalars(select(User))).all() == []
    real = await run(rows)
    assert real["created"] == 2 and {(e["legacy_id"], e["reason"]) for e in real["errors"]} == {("3", "BadRequest"), ("4", "unknown_role"), ("5", "no_totp_secret")}
    again = await run(rows)
    assert again["created"] == 0 and again["skipped_already_imported"] == 2
    dump = str(real) + str(again)
    assert SECRET not in dump and "awa@flariane.ci" not in dump and "hash" not in dump.lower().replace("password_hash", "")


async def test_an_email_already_used_in_auth_is_reported_not_merged(client):
    boss = await admin_headers("super_admin", "0700000002")
    r = await client.post("/v1/admin/staff", headers=boss, json={"email": "awa@flariane.ci", "display_name": "Awa Staff", "product": "flariane", "role": "viewer"})
    assert r.status_code == 201
    stats = await run([arow(5)])
    assert stats["created"] == 0 and stats["errors"] == [{"legacy_id": "5", "reason": "email_taken"}]


async def test_refresh_follows_flariane_until_cutover_then_freezes(client):
    from app.services.legacy_import import import_admin_rows  # noqa: F401

    await run([arow(5)])
    new_secret = pyotp.random_base32()
    changed = hash_password("changed-in-flariane-1")
    stats = await run([arow(5, password_hash=changed, totp_secret=new_secret, role="SUPER_ADMIN", active=True)], refresh=True)
    assert (stats["refresh"]["password"], stats["refresh"]["totp"], stats["refresh"]["role"]) == (1, 1, 1)
    ok = await client.post("/v1/login", json={"email": "awa@flariane.ci", "password": "changed-in-flariane-1", "product": "flariane", "app": "admin", "totp": pyotp.TOTP(new_secret).now()})
    assert ok.status_code == 200 and ok.json()["role"] == "super_admin"
    off = await run([arow(5, password_hash=changed, totp_secret=new_secret, role="SUPER_ADMIN", active=False)], refresh=True)
    assert off["refresh"]["disabled"] == 1
    assert (await client.post("/v1/login", json={"email": "awa@flariane.ci", "password": "changed-in-flariane-1", "product": "flariane", "app": "admin", "totp": pyotp.TOTP(new_secret).now()})).json()["detail"] == "account_blocked"
    async with SessionLocal() as db:
        from sqlalchemy import update
        await db.execute(update(LegacyIdentity).values(credentials_authority="auth"))
        await db.commit()
    frozen = await run([arow(5, password_hash=hash_password("zzz-changed-123"))], refresh=True)
    assert frozen["refresh"]["frozen_skipped"] == 1


async def test_session_check_for_the_product(client):
    await run([arow(5)])
    t = (await client.post("/v1/login", json=body())).json()
    sid = jwt.decode(t["access_token"], options={"verify_signature": False})["sid"]
    key = {"X-Service-Key": "svc-flariane-key"}
    assert (await client.post("/v1/sessions/check", json={"sid": sid})).status_code == 403
    assert (await client.post("/v1/sessions/check", json={"sid": sid}, headers=key)).json() == {"active": True}
    assert (await client.post("/v1/sessions/check", json={"sid": "f" * 32}, headers=key)).json() == {"active": False}
    boss = await admin_headers("super_admin", "0700000002")
    uid = jwt.decode(t["access_token"], options={"verify_signature": False})["sub"]
    assert (await client.post(f"/v1/admin/users/{uid}/disable", headers=boss)).status_code == 204
    assert (await client.post("/v1/sessions/check", json={"sid": sid}, headers=key)).json() == {"active": False}


async def test_staff_can_be_created_with_an_email_only(client):
    boss = await admin_headers("super_admin", "0700000002")
    r = await client.post("/v1/admin/staff", headers=boss, json={"email": "New.Op@Flariane.ci", "display_name": "New Op", "product": "flariane", "role": "pickup"})
    out = r.json()
    assert r.status_code == 201 and out["totp"]["secret"]
    login = await client.post("/v1/login", json={"email": "new.op@flariane.ci", "password": out["temporary_password"], "product": "flariane", "app": "admin", "totp": pyotp.TOTP(out["totp"]["secret"]).now()})
    assert login.status_code == 200 and login.json()["role"] == "pickup" and login.json()["must_change_password"] is True
    assert (await client.post("/v1/admin/staff", headers=boss, json={"email": "new.op@flariane.ci", "display_name": "Dup", "product": "flariane", "role": "viewer"})).json()["detail"] == "email_taken"
    assert (await client.post("/v1/admin/staff", headers=boss, json={"display_name": "Nobody", "product": "flariane", "role": "viewer"})).json()["detail"] == "phone_or_email"
