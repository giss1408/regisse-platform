"""A product's own backend manages its staff through the service key (the supermarket platform adding a retailer's staff), and the
product's admin role (platform_admin) runs its relay. Roles come from the product's configuration."""

import uuid

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.models import AuditLog, Membership, User
from app.services import accounts
from tests.conftest import PASSWORD, code_from_url, make_user
from tests.test_product_admin import product_admin

SMK = {"X-Service-Key": "svc-supermarket-key"}
KEYS = '{"supermarket": "svc-supermarket-key", "fidelia": "svc-fidelia-key", "flariane": "svc-flariane-key"}'
RETAILER = {"product": "supermarket", "app": "retailer"}
CUSTOMER = {"product": "supermarket", "app": "customer"}


@pytest.fixture(autouse=True)
def keys(monkeypatch):
    monkeypatch.setattr(get_settings(), "service_keys_json", KEYS)


async def add_staff(client, phone="0701010101", role="retailer_staff", headers=SMK, name="Awa Caisse"):
    return await client.post("/v1/service/staff", json={"phone": phone, "display_name": name, "role": role}, headers=headers)


async def test_a_new_staff_member_gets_a_temporary_password_and_signs_in_to_the_retailer_app(client):
    r = await add_staff(client)
    assert r.status_code == 201
    body = r.json()
    assert body["existing"] is False and body["temporary_password"] and body["totp"] is None
    login = await client.post("/v1/login", json={"phone": "0701010101", "password": body["temporary_password"], **RETAILER})
    assert login.status_code == 200 and login.json()["role"] == "retailer_staff" and login.json()["must_change_password"] is True
    # staff never use the WhatsApp relay: a reset request is a silent no-op
    await client.post("/v1/password/reset-request", json={"phone": "0701010101", "product": "supermarket"})
    boss = await product_admin("supermarket", role="platform_admin", phone="0700000077")
    assert [x for x in (await client.get("/v1/admin/relay-queue", headers=boss)).json() if x["purpose"] == "password_reset"] == []
    async with SessionLocal() as db:
        row = (await db.scalars(select(AuditLog).where(AuditLog.action == "staff.created"))).all()[-1]
        assert row.actor_type == "service" and "0701010101" not in str(row.detail)


async def test_only_the_roles_the_product_lists_may_be_created_and_only_for_its_own_product(client):
    assert (await add_staff(client, role="platform_admin")).status_code == 403  # never its admins
    assert (await add_staff(client, role="super_admin")).status_code == 403
    assert (await add_staff(client, headers={"X-Service-Key": "svc-fidelia-key"})).status_code == 403  # fidelia lists no service staff roles
    assert (await add_staff(client, headers={"X-Service-Key": "wrong"})).status_code == 403
    assert (await client.post("/v1/service/staff", json={"phone": "0701010101", "display_name": "X Y", "role": "retailer_staff"})).status_code == 403


async def test_a_customer_of_the_product_gains_the_role_and_keeps_her_own_password(client):
    await make_user("0702020202", role="customer", product="supermarket")
    r = await add_staff(client, phone="0702020202")
    assert r.status_code == 201 and r.json()["existing"] is True and r.json()["temporary_password"] is None
    assert (await client.post("/v1/login", json={"phone": "0702020202", "password": PASSWORD, **RETAILER})).json()["role"] == "retailer_staff"
    assert (await client.post("/v1/login", json={"phone": "0702020202", "password": PASSWORD, **CUSTOMER})).status_code == 200


async def test_a_number_that_belongs_to_someone_else_is_refused(client):
    await make_user("0703030303", role="customer", product="flariane")  # a person of ANOTHER product
    assert (await add_staff(client, phone="0703030303")).json()["detail"] == "phone_taken"
    await make_user("0704040404", role="platform_admin", product="supermarket")  # already privileged
    assert (await add_staff(client, phone="0704040404")).json()["detail"] == "phone_taken"
    await add_staff(client, phone="0705050505")
    assert (await add_staff(client, phone="0705050505")).json()["detail"] == "phone_taken"  # a staff member already: not a customer to upgrade


async def test_reset_gives_a_new_temporary_password_and_ends_the_sessions(client):
    first = (await add_staff(client)).json()
    t = (await client.post("/v1/login", json={"phone": "0701010101", "password": first["temporary_password"], **RETAILER})).json()
    r = await client.post(f"/v1/service/staff/{first['user_id']}/reset", headers=SMK)
    assert r.status_code == 200 and r.json()["temporary_password"] != first["temporary_password"]
    assert (await client.post("/v1/refresh", json={"refresh_token": t["refresh_token"]})).status_code == 401
    assert (await client.post("/v1/login", json={"phone": "0701010101", "password": r.json()["temporary_password"], **RETAILER})).status_code == 200
    # a customer of the product is not "staff": the key cannot reset her password; nor can another product's key reach staff at all
    cust = await make_user("0706060606", role="customer", product="supermarket")
    assert (await client.post(f"/v1/service/staff/{cust.id}/reset", headers=SMK)).status_code == 404
    assert (await client.post(f"/v1/service/staff/{first['user_id']}/reset", headers={"X-Service-Key": "svc-fidelia-key"})).status_code == 404
    assert (await client.post(f"/v1/service/staff/{uuid.uuid4()}/reset", headers=SMK)).status_code == 404


async def test_removing_staff_takes_the_role_away_and_switches_off_an_account_left_with_nothing(client):
    gone = (await add_staff(client, phone="0701010101")).json()
    t = (await client.post("/v1/login", json={"phone": "0701010101", "password": gone["temporary_password"], **RETAILER})).json()
    assert (await client.delete(f"/v1/service/staff/{gone['user_id']}", headers=SMK)).json() == {"result": "disabled"}
    assert (await client.post("/v1/refresh", json={"refresh_token": t["refresh_token"]})).status_code == 401
    assert (await client.post("/v1/login", json={"phone": "0701010101", "password": gone["temporary_password"], **RETAILER})).json()["detail"] == "account_blocked"
    # a customer who was upgraded keeps being a customer
    await make_user("0702020202", role="customer", product="supermarket")
    up = (await add_staff(client, phone="0702020202")).json()
    assert (await client.delete(f"/v1/service/staff/{up['user_id']}", headers=SMK)).json() == {"result": "role_removed"}
    assert (await client.post("/v1/login", json={"phone": "0702020202", "password": PASSWORD, **RETAILER})).json()["detail"] == "no_access_to_app"
    assert (await client.post("/v1/login", json={"phone": "0702020202", "password": PASSWORD, **CUSTOMER})).status_code == 200


async def test_a_product_that_knows_only_the_auth_user_id_can_erase_by_it(client):
    cust = await make_user("0707070707", role="customer", product="supermarket")
    other = await make_user("0708080808", role="customer", product="flariane")
    both = {"phone": "0707070707", "user_id": str(cust.id)}
    assert (await client.post("/v1/service/erase", json=both, headers=SMK)).status_code == 400
    assert (await client.post("/v1/service/erase", json={}, headers=SMK)).status_code == 400
    assert (await client.post("/v1/service/erase", json={"user_id": str(other.id)}, headers=SMK)).json() == {"result": "unknown"}  # not her product
    assert (await client.post("/v1/service/erase", json={"user_id": str(cust.id)}, headers=SMK)).json() == {"result": "erased"}
    assert (await client.post("/v1/login", json={"phone": "0707070707", "password": PASSWORD, **CUSTOMER})).status_code == 401
    assert (await client.post("/v1/service/erase", json={"user_id": str(cust.id)}, headers=SMK)).json() == {"result": "unknown"}
    staff = (await add_staff(client)).json()
    assert (await client.post("/v1/service/erase", json={"user_id": staff["user_id"]}, headers=SMK)).json() == {"result": "staff_refused"}
    async with SessionLocal() as db:
        u = await db.get(User, cust.id)
        assert u.phone_enc is None and u.email_enc is None and u.display_name is None


async def test_the_supermarket_platform_admin_runs_the_products_relay_and_reviews(client):
    # customers register phone-first, like everywhere else
    assert (await client.post("/v1/register", json={"phone": "0709090909", "password": "Super-Pass-2026x", "product": "supermarket", "consent": True})).status_code == 201
    boss = await product_admin("supermarket", role="platform_admin", phone="0700000077")
    queue = (await client.get("/v1/admin/relay-queue", headers=boss)).json()
    assert [(x["purpose"], x["product"]) for x in queue] == [("activation", "supermarket")]
    code = code_from_url((await client.post(f"/v1/admin/relay/{queue[0]['id']}/send", headers=boss)).json()["url"])
    assert (await client.post("/v1/activate", json={"phone": "0709090909", "code": code})).status_code == 200
    assert (await client.post("/v1/login", json={"phone": "0709090909", "password": "Super-Pass-2026x", **CUSTOMER})).status_code == 200
    # a retailer's staff member is NOT a relay admin, and the platform admin may give back the product's staff accounts
    staff = (await add_staff(client)).json()
    staff_token = (await client.post("/v1/login", json={"phone": "0701010101", "password": staff["temporary_password"], **RETAILER})).json()["access_token"]
    assert (await client.get("/v1/admin/relay-queue", headers={"Authorization": f"Bearer {staff_token}"})).status_code == 403
    async with SessionLocal() as db:
        (await db.get(User, uuid.UUID(staff["user_id"]))).status = "disabled"
        await db.commit()
    ok = await client.post(f"/v1/admin/users/{staff['user_id']}/enable", headers=boss, json={"note": "Appelée, c'est bien elle, caissière"})
    assert ok.status_code == 204


async def test_the_platform_admin_needs_the_authenticator_to_sign_in(client):
    r = await add_staff(client, phone="0701010101")  # a retailer staff needs no code
    assert r.json()["totp"] is None
    await make_user("0704040404", role="platform_admin", product="supermarket")  # no authenticator set up
    out = await client.post("/v1/login", json={"phone": "0704040404", "password": PASSWORD, "product": "supermarket", "app": "admin"})
    assert out.status_code == 403 and out.json()["detail"] == "totp_not_set_up"
