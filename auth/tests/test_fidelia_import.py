"""Fidelia's users into auth: several roles per person, argon2id hashes kept, SMS-era accounts, admins needing a code, refresh of roles."""

from datetime import datetime

import jwt
import pyotp
from regisse_kit.crypto import hash_password
from sqlalchemy import select

from app.db import SessionLocal
from app.models import LegacyIdentity, RelayRequest
from app.services import accounts
from app.services.legacy_import import import_rows
from tests.conftest import admin_headers

PW = "fidelia-pass-2026"
NOW = datetime(2026, 11, 1, 10, 0, 0)


def row(i, phone, roles=("customer",), status="active", password=True, **over):
    return {"id": i, "phone": phone, "display_name": None, "password_hash": hash_password(PW) if password else None, "status": status, "roles": list(roles),
            "must_change_password": False, "temp_password_expires_at": None, "phone_verified_at": NOW if status == "active" else None, "consent_at": None,
            "consent_version": None, "created_at": NOW, "reset_requested": False, **over}


async def run(rows, apply=True, refresh=False):
    async with SessionLocal() as db:
        stats = await import_rows(db, rows, product="fidelia", role="customer", apply=apply, refresh=refresh)
        await db.commit()
    return stats


async def login(client, phone, app, password=PW, **extra):
    return await client.post("/v1/login", json={"phone": phone, "password": password, "product": "fidelia", "app": app, **extra})


async def test_a_merchant_who_also_shops_keeps_both_roles_and_her_fidelia_password(client):
    stats = await run([row(1, "+2250701020304", roles=("customer", "merchant"))])
    assert stats["created"] == 1 and stats["errors"] == []
    m = await login(client, "0701020304", "merchant")
    c = await login(client, "0701020304", "customer")
    assert m.status_code == c.status_code == 200
    claims = jwt.decode(m.json()["access_token"], options={"verify_signature": False})
    assert claims["role"] == "merchant" and sorted(claims["roles"]) == ["customer", "merchant"] and claims["aud"] == "fidelia"
    assert claims["ph"] == "tel:+2250701020304"  # the key fidelia stores its data under
    async with SessionLocal() as db:
        assert (await db.scalar(select(LegacyIdentity).where(LegacyIdentity.product == "fidelia"))).legacy_id == "1"


async def test_a_cashier_only_has_the_merchant_app(client):
    await run([row(2, "+2250702020202", roles=("cashier",))])
    assert (await login(client, "0702020202", "merchant")).json()["role"] == "cashier"
    assert (await login(client, "0702020202", "customer")).json()["detail"] == "no_access_to_app"


async def test_sms_era_account_is_active_without_a_password_and_resets_through_the_relay(client):
    await run([row(3, "+2250703030303", password=False, phone_verified_at=NOW)])
    assert (await login(client, "0703030303", "customer")).json()["detail"] == "invalid_credentials"  # nothing to match yet
    r = await client.post("/v1/password/reset-request", json={"phone": "0703030303", "product": "fidelia"})
    assert r.status_code == 202
    boss = await admin_headers("super_admin", "0700000002")
    queue = (await client.get("/v1/admin/relay-queue", headers=boss)).json()
    assert [(q["purpose"], q["product"]) for q in queue] == [("password_reset", "fidelia")]


async def test_a_password_with_an_unproven_number_waits_for_the_activation_code(client):
    stats = await run([row(4, "+2250704040404", status="pending")])
    assert stats["activation_requests"] == 1
    assert (await login(client, "0704040404", "customer")).json()["detail"] == "not_activated"
    boss = await admin_headers("super_admin", "0700000002")
    assert [q["purpose"] for q in (await client.get("/v1/admin/relay-queue", headers=boss)).json()] == ["activation"]


async def test_disabled_accounts_stay_disabled(client):
    await run([row(5, "+2250705050505", status="disabled")])
    assert (await login(client, "0705050505", "customer")).json()["detail"] == "account_blocked"


async def test_an_imported_admin_cannot_sign_in_until_a_super_admin_gives_her_a_code(client):
    await run([row(6, "+2250706060606", roles=("admin",))])
    first = await login(client, "0706060606", "admin")
    assert first.status_code == 403 and first.json()["detail"] == "totp_not_set_up"
    async with SessionLocal() as db:
        uid = str((await accounts.user_by_phone(db, "+2250706060606")).id)
    boss = await admin_headers("super_admin", "0700000002")
    reset = (await client.post(f"/v1/admin/staff/{uid}/reset", headers=boss, json={"reset_totp": True})).json()
    ok = await login(client, "0706060606", "admin", password=reset["temporary_password"], totp=pyotp.TOTP(reset["totp"]["secret"]).now())
    assert ok.status_code == 200 and ok.json()["must_change_password"] is True and ok.json()["role"] == "admin"
    # an admin is never offered the WhatsApp relay for a password reset
    await client.post("/v1/password/reset-request", json={"phone": "0706060606", "product": "fidelia"})
    assert (await client.get("/v1/admin/relay-queue", headers=boss)).json() == []


async def test_an_agent_is_active_at_once_and_never_waits_for_a_relay_code(client):
    stats = await run([row(7, "+2250707070707", roles=("agent",))])
    assert stats["created"] == 1
    r = await login(client, "0707070707", "agent")
    assert r.status_code == 200 and r.json()["role"] == "agent"


async def test_idempotent_dry_run_and_bad_rows(client):
    rows = [row(1, "+2250701020304", roles=("customer", "merchant")), row(2, "not-a-phone")]
    dry = await run(rows, apply=False)
    assert dry["created"] == 1 and len(dry["errors"]) == 1
    async with SessionLocal() as db:
        assert await accounts.user_by_phone(db, "+2250701020304") is None
    real = await run(rows)
    again = await run(rows)
    assert real["created"] == 1 and again["created"] == 0 and again["skipped_already_imported"] == 1
    assert "0701020304" not in str(real) and PW not in str(real)


async def test_refresh_follows_role_changes_in_fidelia_until_the_cutover(client):
    await run([row(1, "+2250701020304", roles=("customer",))])
    old = (await login(client, "0701020304", "customer")).json()
    assert (await login(client, "0701020304", "merchant")).json()["detail"] == "no_access_to_app"
    granted = await run([row(1, "+2250701020304", roles=("customer", "merchant"))], refresh=True)
    assert granted["refresh"]["roles"] == 1
    assert (await login(client, "0701020304", "merchant")).status_code == 200
    revoked = await run([row(1, "+2250701020304", roles=("customer",))], refresh=True)
    assert revoked["refresh"]["roles"] == 1
    assert (await login(client, "0701020304", "merchant")).json()["detail"] == "no_access_to_app"
    assert (await client.post("/v1/refresh", json={"refresh_token": old["refresh_token"]})).status_code == 401  # a role was taken away: sign in again


async def test_staff_accounts_are_not_touched_by_refresh(client):
    await run([row(6, "+2250706060606", roles=("admin",))])
    stats = await run([row(6, "+2250706060606", roles=("customer",))], refresh=True)  # fidelia says: no longer admin
    assert stats["refresh"]["frozen_skipped"] == 1  # staff are managed in auth after the import
    async with SessionLocal() as db:
        user = await accounts.user_by_phone(db, "+2250706060606")
        assert {m.role for m in user.memberships} == {"admin"}
