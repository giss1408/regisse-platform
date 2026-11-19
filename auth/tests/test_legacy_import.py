from datetime import datetime, timedelta

from regisse_kit.crypto import hash_password
from sqlalchemy import select

from app.db import SessionLocal
from app.models import LegacyIdentity, RelayRequest, User
from app.services import accounts
from app.services.legacy_import import import_rows
from tests.conftest import PASSWORD, admin_headers, make_user

NOW = datetime(2026, 11, 1, 10, 0, 0)
HASH = hash_password(PASSWORD)


def row(i, phone, status="active", **over):
    return {"id": i, "phone": phone, "display_name": f"Client {i}", "password_hash": HASH, "status": status, "must_change_password": False,
            "temp_password_expires_at": None, "phone_verified_at": NOW if status != "pending" else None, "consent_at": NOW, "consent_version": "2026-10",
            "created_at": NOW, "reset_requested": False, **over}


async def run(rows, apply=True):
    async with SessionLocal() as db:
        stats = await import_rows(db, rows, product="flariane", apply=apply)
        await db.commit()
    return stats


async def count(model):
    async with SessionLocal() as db:
        return len((await db.scalars(select(model))).all())


async def test_verified_customer_can_log_in_with_her_old_password_and_data_is_carried(client):
    stats = await run([row(7, "+2250701020304")])
    assert stats["created"] == 1 and stats["errors"] == []
    r = await client.post("/v1/login", json={"phone": "0701020304", "password": PASSWORD, "product": "flariane", "app": "customer"})
    assert r.status_code == 200 and r.json()["role"] == "customer"
    async with SessionLocal() as db:
        u = await accounts.user_by_phone(db, "+2250701020304")
        mapping = await db.scalar(select(LegacyIdentity).where(LegacyIdentity.user_id == u.id))
    assert u.display_name == "Client 7" and u.consent_version == "2026-10" and u.created_at == NOW and u.phone_verified_at == NOW
    assert (mapping.product, mapping.legacy_id) == ("flariane", "7")


async def test_statuses_are_mapped(client):
    stats = await run([row(1, "+2250701000001"), row(2, "+2250701000002", "pending"), row(3, "+2250701000003", "disabled"), row(4, None)])
    assert (stats["created"], stats["skipped_anonymised"], stats["activation_requests"]) == (3, 1, 1)
    async def login(p):
        return (await client.post("/v1/login", json={"phone": p, "password": PASSWORD, "product": "flariane", "app": "customer"})).json().get("detail", "ok")
    assert [await login("0701000001"), await login("0701000002"), await login("0701000003")] == ["ok", "not_activated", "account_blocked"]
    admin = await admin_headers()
    queue = (await client.get("/v1/admin/relay-queue", headers=admin)).json()
    assert [(i["purpose"], i["phone_masked"]) for i in queue] == [("activation", "+225 07 •• •• 02")]  # the unverified one shows up for an admin to relay


async def test_password_reset_request_and_temp_password_are_carried(client):
    exp = datetime.utcnow() + timedelta(hours=5)
    await run([row(1, "+2250701000001", reset_requested=True), row(2, "+2250701000002", must_change_password=True, temp_password_expires_at=exp)])
    admin = await admin_headers()
    assert [i["purpose"] for i in (await client.get("/v1/admin/relay-queue", headers=admin)).json()] == ["password_reset"]
    r = await client.post("/v1/login", json={"phone": "0701000002", "password": PASSWORD, "product": "flariane", "app": "customer"})
    assert r.json()["must_change_password"] is True


async def test_second_run_changes_nothing(client):
    rows = [row(1, "+2250701000001"), row(2, "+2250701000002", "pending")]
    first = await run(rows)
    second = await run(rows)
    assert first["created"] == 2 and second["created"] == 0 and second["skipped_already_imported"] == 2
    assert (await count(User), await count(LegacyIdentity), await count(RelayRequest)) == (2, 2, 1)


async def test_dry_run_writes_nothing_but_reports_the_same_plan(client):
    rows = [row(1, "+2250701000001"), row(2, "+2250701000002", "pending"), row(3, None)]
    dry = await run(rows, apply=False)
    assert (await count(User), await count(LegacyIdentity), await count(RelayRequest)) == (0, 0, 0)
    real = await run(rows, apply=True)
    assert {k: dry[k] for k in ("total", "created", "skipped_anonymised", "activation_requests")} == {k: real[k] for k in ("total", "created", "skipped_anonymised", "activation_requests")}


async def test_existing_auth_account_is_linked_and_its_password_is_never_overwritten(client):
    await make_user("0701000001", role="customer", product="fidelia")  # already in auth, password = PASSWORD from make_user
    other = hash_password("flariane-old-password-1")
    stats = await run([row(1, "+2250701000001", password_hash=other)])
    assert stats["linked_existing"] == 1 and stats["created"] == 0
    async with SessionLocal() as db:
        u = await accounts.user_by_phone(db, "+2250701000001")
        assert sorted((m.product, m.role) for m in u.memberships) == [("fidelia", "customer"), ("flariane", "customer")]
    # the auth password still works, the flariane one was not applied
    ok = await client.post("/v1/login", json={"phone": "0701000001", "password": PASSWORD, "product": "flariane", "app": "customer"})
    bad = await client.post("/v1/login", json={"phone": "0701000001", "password": "flariane-old-password-1", "product": "flariane", "app": "customer"})
    assert ok.status_code == 200 and bad.status_code == 401


async def test_a_bad_row_does_not_stop_the_others_and_stats_leak_nothing(client):
    stats = await run([row(1, "+2250701000001"), row(2, "not-a-phone"), row(3, "+2250701000003"), row(4, "+2250701000004", status="weird")])
    assert stats["created"] == 2
    assert {(e["legacy_id"], e["reason"]) for e in stats["errors"]} == {("2", "invalid_phone"), ("4", "KeyError")}
    dump = str(stats)
    assert "0701000001" not in dump and "not-a-phone" not in dump and HASH not in dump
    assert await count(User) == 2  # the failed rows left nothing behind


# --- refresh and cutover (the product is still the credential authority) -------------------------


async def refresh(rows, apply=True):
    async with SessionLocal() as db:
        stats = await import_rows(db, rows, product="flariane", apply=apply, refresh=True)
        await db.commit()
    return stats


async def login(client, phone, password=PASSWORD):
    return await client.post("/v1/login", json={"phone": phone, "password": password, "product": "flariane", "app": "customer"})


async def test_refresh_pulls_a_new_password_a_new_phone_and_a_new_name(client):
    await run([row(1, "+2250701000001")])
    old_session = (await login(client, "0701000001")).json()
    new_hash = hash_password("changed-in-flariane-1")
    stats = await refresh([row(1, "+2250709999999", password_hash=new_hash, display_name="Awa Koné")])
    assert (stats["refresh"]["phone"], stats["refresh"]["password"], stats["refresh"]["name"]) == (1, 1, 1)
    assert (await login(client, "0701000001")).status_code == 401  # the old number no longer logs in
    assert (await login(client, "0709999999", PASSWORD)).status_code == 401  # nor the old password
    ok = await login(client, "0709999999", "changed-in-flariane-1")
    assert ok.status_code == 200
    assert (await client.post("/v1/refresh", json={"refresh_token": old_session["refresh_token"]})).status_code == 401  # old sessions ended
    again = await refresh([row(1, "+2250709999999", password_hash=new_hash, display_name="Awa Koné")])
    assert again["refresh"]["unchanged"] == 1 and again["refresh"]["phone"] == 0  # nothing left to do


async def test_the_token_carries_the_legacy_id_for_migrated_users_only(client):
    import jwt as pyjwt

    await run([row(42, "+2250701000001")])
    await make_user("0702000002", role="customer")  # native auth user, no legacy account
    claims = pyjwt.decode((await login(client, "0701000001")).json()["access_token"], options={"verify_signature": False})
    assert claims["lid"] == "42"
    native = pyjwt.decode((await login(client, "0702000002")).json()["access_token"], options={"verify_signature": False})
    assert "lid" not in native
    # after a phone change the claim still points at the same flariane customer
    await refresh([row(42, "+2250708888888")])
    claims = pyjwt.decode((await login(client, "0708888888")).json()["access_token"], options={"verify_signature": False})
    assert claims["lid"] == "42" and claims["ph"] == "tel:+2250708888888"


async def test_a_password_set_in_auth_is_never_overwritten_by_refresh(client):
    await run([row(1, "+2250701000001")])
    t = (await login(client, "0701000001")).json()
    h = {"Authorization": f"Bearer {t['access_token']}"}
    assert (await client.post("/v1/password/change", headers=h, json={"current_password": PASSWORD, "new_password": "set-in-auth-1234"})).status_code == 204
    stats = await refresh([row(1, "+2250701000001", password_hash=hash_password("flariane-newer-1"), display_name="Other Name")])
    assert stats["refresh"]["frozen_skipped"] == 1
    assert (await login(client, "0701000001", "set-in-auth-1234")).status_code == 200
    assert (await login(client, "0701000001", "flariane-newer-1")).status_code == 401


async def test_blocking_propagates_but_refresh_never_re_enables(client):
    await run([row(1, "+2250701000001"), row(2, "+2250701000002")])
    await refresh([row(1, "+2250701000001", "disabled"), row(2, "+2250701000002")])
    assert (await login(client, "0701000001")).json()["detail"] == "account_blocked"
    await refresh([row(1, "+2250701000001", "active"), row(2, "+2250701000002")])  # unblocked in flariane
    assert (await login(client, "0701000001")).json()["detail"] == "account_blocked"  # an auth admin decides, not a sync


async def test_late_verification_activates_a_pending_account(client):
    await run([row(1, "+2250701000001", "pending")])
    assert (await login(client, "0701000001")).json()["detail"] == "not_activated"
    stats = await refresh([row(1, "+2250701000001", "active")])
    assert stats["refresh"]["activated"] == 1 and (await login(client, "0701000001")).status_code == 200
    admin = await admin_headers()
    assert (await client.get("/v1/admin/relay-queue", headers=admin)).json() == []  # the open activation request was closed


async def test_deleting_the_account_in_the_product_erases_it_in_auth(client):
    await run([row(1, "+2250701000001")])
    t = (await login(client, "0701000001")).json()
    stats = await refresh([row(1, None, display_name=None, password_hash=None)])
    assert stats["refresh"]["erased"] == 1
    assert (await login(client, "0701000001")).status_code == 401
    assert (await client.post("/v1/refresh", json={"refresh_token": t["refresh_token"]})).status_code == 401
    async with SessionLocal() as db:
        u = (await db.scalars(select(User))).one()
    assert u.phone_enc is None and u.phone_hash is None and u.display_name is None and u.password_hash is None and u.status == "disabled"


async def test_phone_taken_by_another_auth_account_is_reported_not_applied(client):
    await run([row(1, "+2250701000001")])
    await make_user("0705000005", role="customer", product="fidelia")
    stats = await refresh([row(1, "+2250705000005")])
    assert stats["errors"] == [{"legacy_id": "1", "reason": "ValueError"}]
    assert (await login(client, "0701000001")).status_code == 200  # untouched


async def test_cutover_freezes_everything_and_dry_run_changes_nothing(client):
    from app.services.legacy_import import cutover

    await run([row(1, "+2250701000001")])
    dry = await refresh([row(1, "+2250701000001", password_hash=hash_password("x-changed-1234"))], apply=False)
    assert dry["refresh"]["password"] == 1 and (await login(client, "0701000001")).status_code == 200  # still the old password
    async with SessionLocal() as db:
        assert await cutover(db, product="flariane") == 1
        await db.commit()
        assert await cutover(db, product="flariane") == 0  # idempotent
    stats = await refresh([row(1, "+2250701000001", password_hash=hash_password("x-changed-1234"))])
    assert stats["refresh"]["frozen_skipped"] == 1 and (await login(client, "0701000001")).status_code == 200


async def test_a_staff_account_is_never_touched_by_refresh(client):
    await run([row(1, "+2250701000001")])
    async with SessionLocal() as db:
        u = await accounts.user_by_phone(db, "+2250701000001")
        from app.models import Membership
        db.add(Membership(user_id=u.id, product="flariane", role="operator"))
        await db.commit()
    stats = await refresh([row(1, "+2250701000001", "disabled", password_hash=hash_password("zzz-changed-123"))])
    assert stats["refresh"]["frozen_skipped"] == 1
