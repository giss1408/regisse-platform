"""A product's own admins (fidelia's team, from the fidelia admin app) run that product's relay and number-change reviews, and only that
product's. Sensitive actions need a recent proof."""

import uuid
from datetime import timedelta

import pyotp
from sqlalchemy import delete

from app import tokens
from app.db import SessionLocal
from app.models import Membership
from app.services import accounts, product_hooks
from tests.conftest import PASSWORD, admin_headers, code_from_url, make_user

FID = {"password": PASSWORD, "product": "fidelia", "app": "customer"}
FLR = {"password": PASSWORD, "product": "flariane", "app": "customer"}


async def product_admin(product="fidelia", role="admin", phone="0700000077", *, age=0, amr=("pwd", "totp")) -> dict:
    u = await make_user(phone, role=role, product=product, totp=pyotp.random_base32(), name="Equipe")
    async with SessionLocal() as db:
        tok = await tokens.issue_access_token(db, sub=u.id, phone_key="tel:" + accounts.normalize_phone(phone), product=product, app="admin", role=role,
                                              roles=[role], amr=list(amr), auth_time=tokens.utcnow() - timedelta(seconds=age), sid=uuid.uuid4().hex,
                                              must_change_password=False, ttl=timedelta(minutes=10))
        await db.commit()
    return {"Authorization": f"Bearer {tok}"}


async def customer(client, phone, login):
    await make_user(phone, role="customer", product=login["product"])
    t = (await client.post("/v1/login", json={"phone": phone, **login})).json()
    return {"Authorization": f"Bearer {t['access_token']}"}


async def request_change(client, h, new):
    return await client.post("/v1/me/phone/request", headers=h, json={"new_phone": new, "current_password": PASSWORD})


async def test_a_product_admin_sees_and_sends_only_her_products_relay_items(client):
    fid, flr = await customer(client, "0701010101", FID), await customer(client, "0702020202", FLR)
    await request_change(client, fid, "0711111111")
    await request_change(client, flr, "0722222222")
    team = await product_admin("fidelia")
    mine = (await client.get("/v1/admin/relay-queue", headers=team)).json()
    assert mine and {x["product"] for x in mine} == {"fidelia"}
    boss = await admin_headers("super_admin", "0700000002")
    everyone = (await client.get("/v1/admin/relay-queue", headers=boss)).json()
    other = [x for x in everyone if x["product"] == "flariane"][0]
    assert (await client.post(f"/v1/admin/relay/{other['id']}/send", headers=team)).status_code == 404  # it does not exist for her
    assert (await client.post(f"/v1/admin/relay/{mine[0]['id']}/send", headers=team)).status_code == 200


async def test_phone_changes_list_shows_what_can_still_be_sent_and_only_for_her_product(client):
    fid, flr = await customer(client, "0701010101", FID), await customer(client, "0702020202", FLR)
    await request_change(client, fid, "0711111111")
    await request_change(client, flr, "0722222222")
    team = await product_admin("fidelia")
    rows = (await client.get("/v1/admin/phone-changes", headers=team)).json()
    assert len(rows) == 1 and rows[0]["product"] == "fidelia" and rows[0]["status"] == "requested"
    assert set(rows[0]["relays"]) == {"code", "alert"} and rows[0]["new_phone_masked"] == "+225 07 •• •• 11"
    assert len((await client.get("/v1/admin/phone-changes", headers=await admin_headers("super_admin", "0700000002"))).json()) == 2


async def test_sensitive_actions_need_a_recent_proof_and_stay_inside_her_product(client, monkeypatch):
    async def hook(*a):
        return True

    monkeypatch.setattr(product_hooks, "phone_changed", hook)
    fid, flr = await customer(client, "0701010101", FID), await customer(client, "0702020202", FLR)
    await request_change(client, fid, "0711111111")
    await request_change(client, flr, "0722222222")
    team = await product_admin("fidelia", phone="0700000077")
    boss = await admin_headers("super_admin", "0700000002")
    ids = {x["product"]: x["id"] for x in (await client.get("/v1/admin/phone-changes", headers=boss)).json()}
    stale = await product_admin("fidelia", phone="0700000078", age=900)
    r = await client.post(f"/v1/admin/phone-changes/{ids['fidelia']}/not-me", headers=stale)
    assert r.status_code == 403 and r.json()["detail"] == "step_up_required"
    assert (await client.post(f"/v1/admin/phone-changes/{ids['flariane']}/not-me", headers=team)).status_code == 404  # not hers
    assert (await client.post(f"/v1/admin/phone-changes/{ids['fidelia']}/not-me", headers=team)).json() == {"result": "cancelled", "frozen": True}


async def test_activate_now_by_a_product_admin_with_a_note(client, monkeypatch):
    moved = []

    async def hook(product, old, new):
        moved.append(product)
        return True

    monkeypatch.setattr(product_hooks, "phone_changed", hook)
    fid = await customer(client, "0701010101", FID)
    await request_change(client, fid, "0711111111")
    team = await product_admin("fidelia")
    row = (await client.get("/v1/admin/phone-changes", headers=team)).json()[0]
    code = code_from_url((await client.post(f"/v1/admin/relay/{row['relays']['code']['id']}/send", headers=team)).json()["url"])
    assert (await client.post("/v1/me/phone/confirm", headers=fid, json={"code": code})).json()["status"] == "scheduled"
    ok = await client.post(f"/v1/admin/phone-changes/{row['id']}/activate-now", headers=team, json={"note": "Appele sur son numero, confirme"})
    assert ok.status_code == 200 and moved == ["fidelia"]
    # a notice is now waiting for the old number: the change stays in the list until it is sent
    rows = (await client.get("/v1/admin/phone-changes", headers=team)).json()
    assert [(r["status"], list(r["relays"])) for r in rows] == [("done", ["notice"])]
    await client.post(f"/v1/admin/relay/{rows[0]['relays']['notice']['id']}/send", headers=team)
    assert (await client.get("/v1/admin/phone-changes", headers=team)).json() == []


async def test_who_may_not(client):
    await customer(client, "0701010101", FID)
    assert (await client.get("/v1/admin/relay-queue", headers=await product_admin("fidelia", role="merchant", phone="0700000079"))).status_code == 403
    assert (await client.get("/v1/admin/phone-changes", headers=await product_admin("fidelia", phone="0700000080", amr=("pwd",)))).status_code == 403  # no authenticator code
    team = await product_admin("fidelia", phone="0700000081")
    assert (await client.get("/v1/admin/phone-changes", headers=team)).status_code == 200
    async with SessionLocal() as db:
        await db.execute(delete(Membership).where(Membership.product == "fidelia", Membership.role == "admin"))
        await db.commit()
    assert (await client.get("/v1/admin/phone-changes", headers=team)).status_code == 403  # demoted: at once, not at token expiry


async def test_pending_means_open_or_sent_and_a_temporary_password_needs_a_recent_proof(client):
    await make_user("0701010101", role="customer", product="fidelia")
    await client.post("/v1/password/reset-request", json={"phone": "0701010101", "product": "fidelia"})
    await client.post("/v1/register", json={"phone": "0703030303", "password": "Pending-pass-2026", "product": "fidelia", "consent": True})
    team = await product_admin("fidelia", phone="0700000077")
    rows = (await client.get("/v1/admin/relay-queue", params={"status": "pending"}, headers=team)).json()
    by = {r["purpose"]: r for r in rows}
    assert set(by) == {"activation", "password_reset"}
    stale = await product_admin("fidelia", phone="0700000078", age=900)
    r = await client.post(f"/v1/admin/relay/{by['password_reset']['id']}/send", headers=stale)
    assert r.status_code == 403 and r.json()["detail"] == "step_up_required"
    assert (await client.post(f"/v1/admin/relay/{by['activation']['id']}/send", headers=stale)).status_code == 200  # an activation code needs no fresh proof
    assert (await client.post(f"/v1/admin/relay/{by['password_reset']['id']}/send", headers=team)).status_code == 200
    again = {r["purpose"] for r in (await client.get("/v1/admin/relay-queue", params={"status": "pending"}, headers=team)).json()}
    assert again == {"activation"}  # sent once and waiting for the code: still listed so it can be re-sent; the reset is done


# --- frozen accounts: list and give back --------------------------------------------------------------------------------


async def _freeze_via_not_me(client, phone, new, monkeypatch):
    async def hook(*a):
        return True

    monkeypatch.setattr(product_hooks, "phone_changed", hook)
    h = await customer(client, phone, FID)
    await request_change(client, h, new)
    boss = await admin_headers("super_admin", "0700000002")
    ref = [x for x in (await client.get("/v1/admin/relay-queue", headers=boss)).json() if x["purpose"] == "phone_change"][-1]["ref_id"]
    assert (await client.post(f"/v1/admin/phone-changes/{ref}/not-me", headers=boss)).status_code == 200
    return h


async def test_frozen_users_are_listed_for_their_product_with_the_reason(client, monkeypatch):
    await _freeze_via_not_me(client, "0701010101", "0711111111", monkeypatch)
    other = await make_user("0702020202", role="customer", product="flariane")
    boss = await admin_headers("super_admin", "0700000003")
    async with SessionLocal() as db:
        u = await db.get(type(other), other.id)
        u.status = "disabled"
        erased = await make_user("0704040404", role="customer", product="fidelia")
        e = await db.get(type(erased), erased.id)
        e.status, e.phone_enc, e.phone_hash = "disabled", None, None
        await db.commit()
    team = await product_admin("fidelia")
    mine = (await client.get("/v1/admin/frozen-users", headers=team)).json()
    assert len(mine) == 1 and mine[0]["products"] == ["fidelia"] and mine[0]["reason"] == "not_me" and mine[0]["phone_masked"] == "+225 07 •• •• 01"
    everyone = (await client.get("/v1/admin/frozen-users", headers=boss)).json()
    assert {tuple(x["products"]) for x in everyone} == {("fidelia",), ("flariane",)}  # the erased account (no number) is never listed
    assert any(x["reason"] == "unknown" for x in everyone)  # disabled without an audit row here: said honestly


async def test_a_product_admin_gives_an_account_back_with_a_note_and_a_recent_proof(client, monkeypatch):
    await _freeze_via_not_me(client, "0701010101", "0711111111", monkeypatch)
    team = await product_admin("fidelia")
    uid = (await client.get("/v1/admin/frozen-users", headers=team)).json()[0]["id"]
    stale = await product_admin("fidelia", phone="0700000078", age=900)
    assert (await client.post(f"/v1/admin/users/{uid}/enable", headers=stale, json={"note": "Appelé, c'est bien lui"})).json()["detail"] == "step_up_required"
    assert (await client.post(f"/v1/admin/users/{uid}/enable", headers=team)).json()["detail"] == "note_required"
    assert (await client.post(f"/v1/admin/users/{uid}/enable", headers=team, json={"note": "court"})).json()["detail"] == "note_required"
    assert (await client.post("/v1/login", json={"phone": "0701010101", **FID})).json()["detail"] == "account_blocked"
    ok = await client.post(f"/v1/admin/users/{uid}/enable", headers=team, json={"note": "Appelé sur l'ancien numéro, c'est bien lui"})
    assert ok.status_code == 204
    assert (await client.post("/v1/login", json={"phone": "0701010101", **FID})).status_code == 200
    assert (await client.get("/v1/admin/frozen-users", headers=team)).json() == []
    from sqlalchemy import select

    from app.models import AuditLog

    async with SessionLocal() as db:
        row = (await db.scalars(select(AuditLog).where(AuditLog.action == "user.enabled"))).all()[-1]
        assert "bien lui" in row.detail["note"]


async def test_a_product_admin_cannot_enable_other_products_people_auth_staff_or_erased_accounts(client):
    team = await product_admin("fidelia")
    note = {"note": "Appelé, tout est vérifié ici"}
    flr = await make_user("0702020202", role="customer", product="flariane")
    staff = await make_user("0703030303", role="admin", product="auth")
    erased = await make_user("0704040404", role="customer", product="fidelia")
    async with SessionLocal() as db:
        for u in (flr, staff, erased):
            row = await db.get(type(u), u.id)
            row.status = "disabled"
        e = await db.get(type(erased), erased.id)
        e.phone_enc, e.phone_hash = None, None
        await db.commit()
    assert (await client.post(f"/v1/admin/users/{flr.id}/enable", headers=team, json=note)).status_code == 404
    assert (await client.post(f"/v1/admin/users/{staff.id}/enable", headers=team, json=note)).status_code == 404  # not in her product: it does not exist for her
    assert (await client.post(f"/v1/admin/users/{erased.id}/enable", headers=team, json=note)).status_code == 409
    # a fidelia admin (not super) cannot give back another fidelia staff account; a fidelia super admin can
    agent = await make_user("0705050505", role="agent", product="fidelia")
    async with SessionLocal() as db:
        (await db.get(type(agent), agent.id)).status = "disabled"
        await db.commit()
    assert (await client.post(f"/v1/admin/users/{agent.id}/enable", headers=team, json=note)).json()["detail"] == "super_admin_only"
    boss = await product_admin("fidelia", role="super_admin", phone="0700000090")
    assert (await client.post(f"/v1/admin/users/{agent.id}/enable", headers=boss, json=note)).status_code == 204


async def test_user_search_by_a_product_admin_stays_in_her_product_and_by_phone(client):
    await make_user("0701010101", role="customer", product="fidelia")
    await make_user("0702020202", role="customer", product="flariane")
    team = await product_admin("fidelia")
    assert (await client.get("/v1/admin/users/search", params={"phone": "0701010101"}, headers=team)).status_code == 200
    assert (await client.get("/v1/admin/users/search", params={"phone": "0702020202"}, headers=team)).status_code == 404
    assert (await client.get("/v1/admin/users/search", params={"email": "x@y.fr"}, headers=team)).status_code == 403
