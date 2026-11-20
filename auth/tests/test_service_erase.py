"""A product erased a person: auth drops her access to that product, and erases the identity when nothing else is left."""

from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.models import AuditLog, Membership, User
from app.services import accounts
from tests.conftest import PASSWORD, make_user

KEYS = '{"flariane": "svc-flariane-key", "fidelia": "svc-fidelia-key"}'
FID = {"X-Service-Key": "svc-fidelia-key"}


async def erase(client, phone, headers=FID):
    return await client.post("/v1/service/erase", json={"phone": phone}, headers=headers)


async def test_a_fidelia_only_customer_is_erased_and_signed_out_everywhere(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "service_keys_json", KEYS)
    await make_user("0701020304", role="customer", product="fidelia")
    t = (await client.post("/v1/login", json={"phone": "0701020304", "password": PASSWORD, "product": "fidelia", "app": "customer"})).json()
    r = await erase(client, "07 01 02 03 04")
    assert r.json() == {"result": "erased"}
    assert (await client.post("/v1/refresh", json={"refresh_token": t["refresh_token"]})).status_code == 401
    assert (await client.post("/v1/login", json={"phone": "0701020304", "password": PASSWORD, "product": "fidelia", "app": "customer"})).status_code in (401, 403)
    async with SessionLocal() as db:
        assert await accounts.user_by_phone(db, "+2250701020304") is None  # the number is gone, not just disabled
        audit = (await db.scalars(select(AuditLog).where(AuditLog.action == "user.erased"))).all()
        assert len(audit) == 1 and "0701020304" not in str(audit[0].detail)
    assert (await erase(client, "0701020304")).json() == {"result": "unknown"}  # idempotent


async def test_a_person_with_another_product_keeps_that_account(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "service_keys_json", KEYS)
    u = await make_user("0702020202", role="customer", product="fidelia")
    async with SessionLocal() as db:
        db.add(Membership(user_id=u.id, product="flariane", role="customer"))
        await db.commit()
    assert (await erase(client, "0702020202")).json() == {"result": "membership_removed"}
    ok = await client.post("/v1/login", json={"phone": "0702020202", "password": PASSWORD, "product": "flariane", "app": "customer"})
    assert ok.status_code == 200
    gone = await client.post("/v1/login", json={"phone": "0702020202", "password": PASSWORD, "product": "fidelia", "app": "customer"})
    assert gone.status_code == 403 and gone.json()["detail"] == "no_access_to_app"


async def test_a_key_only_speaks_for_its_own_product_and_needs_to_exist(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "service_keys_json", KEYS)
    await make_user("0703030303", role="customer", product="flariane")
    assert (await erase(client, "0703030303")).json() == {"result": "unknown"}  # fidelia's key cannot touch a flariane-only person
    async with SessionLocal() as db:
        assert (await accounts.user_by_phone(db, "+2250703030303")).status == "active"
    assert (await erase(client, "0703030303", headers={"X-Service-Key": "wrong"})).status_code == 403
    assert (await client.post("/v1/service/erase", json={"phone": "0703030303"})).status_code == 403


async def test_staff_are_never_erased_this_way(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "service_keys_json", KEYS)
    await make_user("0704040404", role="agent", product="fidelia")
    assert (await erase(client, "0704040404")).json() == {"result": "staff_refused"}
    async with SessionLocal() as db:
        user = await accounts.user_by_phone(db, "+2250704040404")
        assert user.status == "active" and {m.role for m in user.memberships} == {"agent"}


async def test_garbage_numbers_are_answered_like_unknown_ones(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "service_keys_json", KEYS)
    assert (await erase(client, "not a number")).json() == {"result": "unknown"}
