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
