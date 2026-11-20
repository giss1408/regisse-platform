"""A product's own backend enrols its staff through the service key, the way fidelia enrols a merchant: a pending account, the team sends the
WhatsApp activation code, the person chooses her password. The product's admin role (platform_admin) runs its relay. Roles come from the
product's configuration."""

import uuid

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.models import AuditLog, User
from tests.conftest import PASSWORD, code_from_url, make_user
from tests.test_product_admin import product_admin

SMK = {"X-Service-Key": "svc-supermarket-key"}
KEYS = '{"supermarket": "svc-supermarket-key", "fidelia": "svc-fidelia-key", "flariane": "svc-flariane-key"}'
RETAILER = {"product": "supermarket", "app": "retailer"}
CUSTOMER = {"product": "supermarket", "app": "customer"}
CHOSEN = "Caissiere-pass-2026x"


@pytest.fixture(autouse=True)
def keys(monkeypatch):
    monkeypatch.setattr(get_settings(), "service_keys_json", KEYS)


async def enrol(client, phone="0701010101", role="retailer_staff", headers=SMK, name="Awa Caisse"):
    return await client.post("/v1/service/staff", json={"phone": phone, "display_name": name, "role": role}, headers=headers)


async def team_code(client, boss, phone_tail):
    """The activation code the team sends from its own WhatsApp (the relay queue of the product)."""
    q = [x for x in (await client.get("/v1/admin/relay-queue", headers=boss)).json() if x["purpose"] == "activation" and x["phone_masked"].endswith(phone_tail)]
    return code_from_url((await client.post(f"/v1/admin/relay/{q[-1]['id']}/send", headers=boss)).json()["url"])


async def enrolled_and_active(client, boss, phone="0701010101"):
    r = (await enrol(client, phone=phone)).json()
    code = await team_code(client, boss, phone[-2:])
    assert (await client.post("/v1/activate", json={"phone": phone, "code": code, "password": CHOSEN})).status_code == 200
    return r["user_id"]


async def test_enrolment_is_a_pending_account_the_team_activates_by_whatsapp_and_the_person_sets_her_password(client):
    boss = await product_admin("supermarket", role="platform_admin", phone="0700000077")
    r = await enrol(client)
    assert r.status_code == 201 and r.json()["existing"] is False and r.json()["activation"] == "relay" and "temporary_password" not in r.json()
    assert (await client.post("/v1/login", json={"phone": "0701010101", "password": CHOSEN, **RETAILER})).json()["detail"] in ("invalid_credentials", "not_activated")
    queue = (await client.get("/v1/admin/relay-queue", headers=boss)).json()
    assert [(x["purpose"], x["product"]) for x in queue] == [("activation", "supermarket")]  # in the product's queue, the platform admin's to send
    code = await team_code(client, boss, "01")
    # no password yet: refused, and the code is NOT spent; a weak one is refused too
    assert (await client.post("/v1/activate", json={"phone": "0701010101", "code": code})).json()["detail"] == "password_required"
    assert (await client.post("/v1/activate", json={"phone": "0701010101", "code": code, "password": "12345678"})).json()["detail"] == "weak_password"
    assert (await client.post("/v1/activate", json={"phone": "0701010101", "code": code, "password": CHOSEN})).status_code == 200
    login = await client.post("/v1/login", json={"phone": "0701010101", "password": CHOSEN, **RETAILER})
    assert login.status_code == 200 and login.json()["role"] == "retailer_staff" and login.json()["must_change_password"] is False
    assert (await client.post("/v1/activate", json={"phone": "0701010101", "code": code, "password": CHOSEN})).status_code == 400  # a code works once
    async with SessionLocal() as db:
        row = (await db.scalars(select(AuditLog).where(AuditLog.action == "staff.enrolled"))).all()[-1]
        assert row.actor_type == "service" and "0701010101" not in str(row.detail)


async def test_a_password_sent_with_the_code_never_changes_an_existing_one(client):
    boss = await product_admin("supermarket", role="platform_admin", phone="0700000077")
    assert (await client.post("/v1/register", json={"phone": "0709090909", "password": "Customer-pass-2026x", "product": "supermarket", "consent": True})).status_code == 201
    code = await team_code(client, boss, "09")
    assert (await client.post("/v1/activate", json={"phone": "0709090909", "code": code, "password": "Someone-else-2026x"})).status_code == 200
    assert (await client.post("/v1/login", json={"phone": "0709090909", "password": "Customer-pass-2026x", **CUSTOMER})).status_code == 200
    assert (await client.post("/v1/login", json={"phone": "0709090909", "password": "Someone-else-2026x", **CUSTOMER})).status_code == 401


async def test_only_the_roles_the_product_lists_may_be_enrolled_and_only_for_its_own_product(client):
    assert (await enrol(client, role="platform_admin")).status_code == 403  # never its admins
    assert (await enrol(client, role="super_admin")).status_code == 403
    assert (await enrol(client, headers={"X-Service-Key": "svc-fidelia-key"})).status_code == 403  # fidelia lists no service staff roles
    assert (await enrol(client, headers={"X-Service-Key": "wrong"})).status_code == 403
    assert (await client.post("/v1/service/staff", json={"phone": "0701010101", "display_name": "X Y", "role": "retailer_staff"})).status_code == 403


async def test_a_customer_of_the_product_gains_the_role_and_keeps_her_own_password(client):
    await make_user("0702020202", role="customer", product="supermarket")
    r = await enrol(client, phone="0702020202")
    assert r.status_code == 201 and r.json()["existing"] is True and r.json()["activation"] is None
    assert (await client.post("/v1/login", json={"phone": "0702020202", "password": PASSWORD, **RETAILER})).json()["role"] == "retailer_staff"
    assert (await client.post("/v1/login", json={"phone": "0702020202", "password": PASSWORD, **CUSTOMER})).status_code == 200
    assert (await enrol(client, phone="0702020202")).json()["existing"] is True  # asking twice changes nothing


async def test_a_number_that_belongs_to_someone_else_is_refused(client):
    await make_user("0703030303", role="customer", product="flariane")  # a person of ANOTHER product
    assert (await enrol(client, phone="0703030303")).json()["detail"] == "phone_taken"
    await make_user("0704040404", role="platform_admin", product="supermarket")  # already privileged
    assert (await enrol(client, phone="0704040404")).json()["detail"] == "phone_taken"
    await make_user("0705050505", role="customer", product="supermarket", status="disabled")
    assert (await enrol(client, phone="0705050505")).json()["detail"] == "phone_taken"  # a switched-off account is not quietly revived
    await enrol(client, phone="0706060606")
    assert (await enrol(client, phone="0706060606")).json()["detail"] == "phone_taken"  # already enrolled, not yet active


async def test_staff_who_forget_their_password_use_the_relay_like_merchants_do(client):
    boss = await product_admin("supermarket", role="platform_admin", phone="0700000077")
    await enrolled_and_active(client, boss)
    await client.post("/v1/password/reset-request", json={"phone": "0701010101", "product": "supermarket"})
    item = [x for x in (await client.get("/v1/admin/relay-queue", headers=boss)).json() if x["purpose"] == "password_reset"]
    assert len(item) == 1
    from tests.conftest import temp_from_url

    temp = temp_from_url((await client.post(f"/v1/admin/relay/{item[0]['id']}/send", headers=boss)).json()["url"])
    login = await client.post("/v1/login", json={"phone": "0701010101", "password": temp, **RETAILER})
    assert login.status_code == 200 and login.json()["must_change_password"] is True


async def test_removing_staff_takes_the_role_away_and_switches_off_an_account_left_with_nothing(client):
    boss = await product_admin("supermarket", role="platform_admin", phone="0700000077")
    uid = await enrolled_and_active(client, boss)
    t = (await client.post("/v1/login", json={"phone": "0701010101", "password": CHOSEN, **RETAILER})).json()
    assert (await client.delete(f"/v1/service/staff/{uid}", headers=SMK)).json() == {"result": "disabled"}
    assert (await client.post("/v1/refresh", json={"refresh_token": t["refresh_token"]})).status_code == 401
    assert (await client.post("/v1/login", json={"phone": "0701010101", "password": CHOSEN, **RETAILER})).json()["detail"] == "account_blocked"
    # a customer who was upgraded keeps being a customer
    await make_user("0702020202", role="customer", product="supermarket")
    up = (await enrol(client, phone="0702020202")).json()
    assert (await client.delete(f"/v1/service/staff/{up['user_id']}", headers=SMK)).json() == {"result": "role_removed"}
    assert (await client.post("/v1/login", json={"phone": "0702020202", "password": PASSWORD, **RETAILER})).json()["detail"] == "no_access_to_app"
    assert (await client.post("/v1/login", json={"phone": "0702020202", "password": PASSWORD, **CUSTOMER})).status_code == 200
    # another product's key, and a plain customer, are not "staff" for this call
    other = await make_user("0707070707", role="customer", product="supermarket")
    assert (await client.delete(f"/v1/service/staff/{other.id}", headers=SMK)).status_code == 404
    assert (await client.delete(f"/v1/service/staff/{uid}", headers={"X-Service-Key": "svc-fidelia-key"})).status_code == 404
    assert (await client.delete(f"/v1/service/staff/{uuid.uuid4()}", headers=SMK)).status_code == 404


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
    admin = await make_user("0704040404", role="platform_admin", product="supermarket")
    assert (await client.post("/v1/service/erase", json={"user_id": str(admin.id)}, headers=SMK)).json() == {"result": "staff_refused"}
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
    # a retailer's staff member is NOT a relay admin, and the platform admin gives back and switches off the product's staff accounts
    staff_id = await enrolled_and_active(client, boss)
    staff_token = (await client.post("/v1/login", json={"phone": "0701010101", "password": CHOSEN, **RETAILER})).json()["access_token"]
    assert (await client.get("/v1/admin/relay-queue", headers={"Authorization": f"Bearer {staff_token}"})).status_code == 403
    note = {"note": "Caissière absente, compte suspendu le temps de vérifier"}
    assert (await client.post(f"/v1/admin/users/{staff_id}/disable", headers=boss, json=note)).status_code == 204
    assert (await client.post("/v1/login", json={"phone": "0701010101", "password": CHOSEN, **RETAILER})).json()["detail"] == "account_blocked"
    assert (await client.post(f"/v1/admin/users/{staff_id}/enable", headers=boss, json={"note": "Appelée, c'est bien elle, caissière"})).status_code == 204
    assert (await client.post("/v1/login", json={"phone": "0701010101", "password": CHOSEN, **RETAILER})).status_code == 200


async def test_the_platform_admin_needs_the_authenticator_to_sign_in(client):
    await make_user("0704040404", role="platform_admin", product="supermarket")  # no authenticator set up
    out = await client.post("/v1/login", json={"phone": "0704040404", "password": PASSWORD, "product": "supermarket", "app": "admin"})
    assert out.status_code == 403 and out.json()["detail"] == "totp_not_set_up"
