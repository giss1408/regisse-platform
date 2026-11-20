"""Number change in auth: password + code sent to the NEW number by the relay admin; products move their data first."""

import jwt
import pytest
from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.models import AuditLog, RelayRequest
from app.services import accounts, product_hooks
from tests.conftest import PASSWORD, admin_headers, code_from_url, make_user

LOGIN = {"password": PASSWORD, "product": "fidelia", "app": "customer"}


@pytest.fixture(autouse=True)
def no_delay(monkeypatch):
    """The process tests below run without the security delay; the delay has its own tests at the end."""
    monkeypatch.setattr(get_settings(), "phone_change_delay_hours", 0)


async def signed_in(client, phone="0701020304", **user):
    await make_user(phone, role="customer", product="fidelia", **user)
    t = (await client.post("/v1/login", json={"phone": phone, **LOGIN})).json()
    return t, {"Authorization": f"Bearer {t['access_token']}"}


async def relay_code(client, boss) -> str:
    q = (await client.get("/v1/admin/relay-queue", headers=boss)).json()
    item = [x for x in q if x["purpose"] == "phone_change"][-1]
    return code_from_url((await client.post(f"/v1/admin/relay/{item['id']}/send", headers=boss)).json()["url"]), item


async def test_the_whole_process_moves_the_number_ends_other_sessions_and_keeps_this_one(client, monkeypatch):
    moved = []

    async def hook(product, old, new):
        moved.append((product, old, new))
        return True

    monkeypatch.setattr(product_hooks, "phone_changed", hook)
    t, h = await signed_in(client)
    other = (await client.post("/v1/login", json={"phone": "0701020304", **LOGIN})).json()
    boss = await admin_headers("super_admin", "0700000002")
    r = await client.post("/v1/me/phone/request", headers=h, json={"new_phone": "07 05 06 07 08", "current_password": PASSWORD})
    assert r.status_code == 202 and r.json()["new_phone_masked"] == "+225 07 •• •• 08"
    code, item = await relay_code(client, boss)
    assert item["product"] == "fidelia"
    ok = await client.post("/v1/me/phone/confirm", headers=h, json={"code": code})
    assert ok.status_code == 200, ok.text
    assert moved == [("fidelia", "+2250701020304", "+2250705060708")]
    # the other device is out; this one renews and now carries the new number
    assert (await client.post("/v1/refresh", json={"refresh_token": other["refresh_token"]})).status_code == 401
    renewed = (await client.post("/v1/refresh", json={"refresh_token": t["refresh_token"]})).json()
    assert jwt.decode(renewed["access_token"], options={"verify_signature": False})["ph"] == "tel:+2250705060708"
    # old number no longer signs in, new one does
    assert (await client.post("/v1/login", json={"phone": "0701020304", **LOGIN})).status_code == 401
    assert (await client.post("/v1/login", json={"phone": "0705060708", **LOGIN})).status_code == 200
    async with SessionLocal() as db:
        assert (await db.scalars(select(AuditLog).where(AuditLog.action == "user.phone_changed"))).all()
        assert all("0705060708" not in str(a.detail) for a in (await db.scalars(select(AuditLog))).all())
    # a code works once
    assert (await client.post("/v1/me/phone/confirm", headers=h, json={"code": code})).status_code == 400


async def test_the_password_is_required_and_a_wrong_one_counts_like_a_login_failure(client):
    _, h = await signed_in(client)
    r = await client.post("/v1/me/phone/request", headers=h, json={"new_phone": "0705060708", "current_password": "wrong-password-1"})
    assert r.status_code == 401 and r.json()["detail"] == "invalid_credentials"
    async with SessionLocal() as db:
        assert (await db.scalars(select(RelayRequest).where(RelayRequest.purpose == "phone_change"))).all() == []


async def test_a_number_that_already_has_an_account_or_her_own_is_refused(client):
    _, h = await signed_in(client)
    await make_user("0709090909", role="customer", product="fidelia")
    taken = await client.post("/v1/me/phone/request", headers=h, json={"new_phone": "0709090909", "current_password": PASSWORD})
    assert taken.status_code == 409 and taken.json()["detail"] == "phone_taken"
    same = await client.post("/v1/me/phone/request", headers=h, json={"new_phone": "0701020304", "current_password": PASSWORD})
    assert same.status_code == 400 and same.json()["detail"] == "same_phone"


async def test_the_code_goes_to_the_new_number_and_a_wrong_code_changes_nothing(client, monkeypatch):
    async def hook(*a):
        raise AssertionError("no product may move on a wrong code")

    monkeypatch.setattr(product_hooks, "phone_changed", hook)
    _, h = await signed_in(client)
    boss = await admin_headers("super_admin", "0700000002")
    await client.post("/v1/me/phone/request", headers=h, json={"new_phone": "0705060708", "current_password": PASSWORD})
    q = [x for x in (await client.get("/v1/admin/relay-queue", headers=boss)).json() if x["purpose"] == "phone_change"]
    url = (await client.post(f"/v1/admin/relay/{q[-1]['id']}/send", headers=boss)).json()["url"]
    assert "2250705060708" in url and "2250701020304" not in url  # the wa.me link targets the NEW number
    bad = await client.post("/v1/me/phone/confirm", headers=h, json={"code": "000000"})
    assert bad.status_code == 400 and bad.json()["detail"] == "wrong_activation_code"
    assert (await client.post("/v1/login", json={"phone": "0701020304", **LOGIN})).status_code == 200


async def test_a_new_request_replaces_the_old_one_and_its_code(client, monkeypatch):
    async def hook(*a):
        return True

    monkeypatch.setattr(product_hooks, "phone_changed", hook)
    _, h = await signed_in(client)
    boss = await admin_headers("super_admin", "0700000002")
    await client.post("/v1/me/phone/request", headers=h, json={"new_phone": "0705060708", "current_password": PASSWORD})
    first, _ = await relay_code(client, boss)
    await client.post("/v1/me/phone/request", headers=h, json={"new_phone": "0706060606", "current_password": PASSWORD})
    assert (await client.post("/v1/me/phone/confirm", headers=h, json={"code": first})).status_code == 400  # the first code died with the first request
    second, _ = await relay_code(client, boss)
    assert (await client.post("/v1/me/phone/confirm", headers=h, json={"code": second})).status_code == 200
    assert (await client.post("/v1/login", json={"phone": "0706060606", **LOGIN})).status_code == 200


async def test_a_product_that_cannot_move_its_data_stops_the_change_and_the_code_stays_valid(client, monkeypatch):
    calls = {"n": 0}

    async def hook(product, old, new):
        calls["n"] += 1
        if calls["n"] == 1:
            raise product_hooks.HookFailed(product)
        return True

    monkeypatch.setattr(product_hooks, "phone_changed", hook)
    _, h = await signed_in(client)
    boss = await admin_headers("super_admin", "0700000002")
    await client.post("/v1/me/phone/request", headers=h, json={"new_phone": "0705060708", "current_password": PASSWORD})
    code, _ = await relay_code(client, boss)
    down = await client.post("/v1/me/phone/confirm", headers=h, json={"code": code})
    assert down.status_code == 503 and down.json()["detail"] == "product_unavailable"
    assert (await client.post("/v1/login", json={"phone": "0701020304", **LOGIN})).status_code == 200  # nothing changed
    assert (await client.post("/v1/me/phone/confirm", headers=h, json={"code": code})).status_code == 200  # same code, product back


async def test_staff_do_not_change_numbers_this_way_and_a_disabled_user_cannot(client):
    h = await admin_headers("super_admin", "0700000002")
    r = await client.post("/v1/me/phone/request", headers=h, json={"new_phone": "0705060708", "current_password": PASSWORD})
    assert r.status_code == 403


async def test_the_new_number_is_taken_in_the_meantime(client, monkeypatch):
    async def hook(*a):
        return True

    monkeypatch.setattr(product_hooks, "phone_changed", hook)
    _, h = await signed_in(client)
    boss = await admin_headers("super_admin", "0700000002")
    await client.post("/v1/me/phone/request", headers=h, json={"new_phone": "0705060708", "current_password": PASSWORD})
    code, _ = await relay_code(client, boss)
    await make_user("0705060708", role="customer", product="fidelia")  # someone signs up with it first
    r = await client.post("/v1/me/phone/confirm", headers=h, json={"code": code})
    assert r.status_code == 409 and r.json()["detail"] == "phone_taken"
    async with SessionLocal() as db:
        assert accounts.user_phone(await accounts.user_by_phone(db, "+2250701020304")) == "+2250701020304"


# --- "this wasn't me": the old number is warned, an admin cancels or puts the number back ---------------------------------


def _queue(rows, purpose):
    return [x for x in rows if x["purpose"] == purpose]


async def _request(client, h, new="0705060708"):
    return await client.post("/v1/me/phone/request", headers=h, json={"new_phone": new, "current_password": PASSWORD})


async def test_the_old_number_is_warned_the_moment_a_change_is_requested(client):
    _, h = await signed_in(client)
    boss = await admin_headers("super_admin", "0700000002")
    await _request(client, h)
    alert = _queue((await client.get("/v1/admin/relay-queue", headers=boss)).json(), "phone_change_alert")[0]
    assert alert["ref_id"] is not None and alert["product"] == "fidelia"
    url = (await client.post(f"/v1/admin/relay/{alert['id']}/send", headers=boss)).json()["url"]
    assert "2250701020304" in url and "2250705060708" not in url  # to the number being left
    from urllib.parse import parse_qs, urlsplit

    text = parse_qs(urlsplit(url).query)["text"][0]
    assert "PAS MOI" in text and "+225 07 •• •• 08" in text and "0705060708" not in text  # the new number is masked, no code in it
    assert (await client.post(f"/v1/admin/relay/{alert['id']}/send", headers=boss)).status_code == 409  # sent once


async def test_a_confirmed_change_leaves_a_notice_for_the_old_number_and_the_stale_warning_is_refused(client, monkeypatch):
    async def hook(*a):
        return True

    monkeypatch.setattr(product_hooks, "phone_changed", hook)
    _, h = await signed_in(client)
    boss = await admin_headers("super_admin", "0700000002")
    await _request(client, h)
    code, _ = await relay_code(client, boss)
    assert (await client.post("/v1/me/phone/confirm", headers=h, json={"code": code})).status_code == 200
    rows = (await client.get("/v1/admin/relay-queue", headers=boss)).json()
    notice = _queue(rows, "phone_changed_notice")[0]
    url = (await client.post(f"/v1/admin/relay/{notice['id']}/send", headers=boss)).json()["url"]
    assert "2250701020304" in url  # to the OLD number, although the account's number is now the new one
    assert _queue(rows, "phone_change_alert") == []  # the warning left the open queue with the change
    every = (await client.get("/v1/admin/relay-queue", params={"status": "all"}, headers=boss)).json()
    stale = _queue(every, "phone_change_alert")[0]
    assert (await client.post(f"/v1/admin/relay/{stale['id']}/send", headers=boss)).status_code == 409


async def test_not_me_on_a_pending_request_cancels_it_freezes_the_account_and_kills_the_code(client, monkeypatch):
    async def hook(*a):
        raise AssertionError("nothing moved, nothing to move back")

    monkeypatch.setattr(product_hooks, "phone_changed", hook)
    t, h = await signed_in(client)
    boss = await admin_headers("super_admin", "0700000002")
    await _request(client, h)
    code, item = await relay_code(client, boss)
    r = await client.post(f"/v1/admin/phone-changes/{item['ref_id']}/not-me", headers=boss)
    assert r.json() == {"result": "cancelled", "frozen": True}
    assert (await client.post("/v1/login", json={"phone": "0701020304", **LOGIN})).json()["detail"] == "account_blocked"
    assert (await client.post("/v1/refresh", json={"refresh_token": t["refresh_token"]})).status_code == 401
    # an admin calls the person, then re-enables: the number is still the old one and the code is dead
    async with SessionLocal() as db:
        uid = str((await accounts.user_by_phone(db, "+2250701020304")).id)
    assert (await client.post(f"/v1/admin/users/{uid}/enable", headers=boss)).status_code == 204
    again = (await client.post("/v1/login", json={"phone": "0701020304", **LOGIN})).json()
    assert (await client.post("/v1/me/phone/confirm", headers={"Authorization": f"Bearer {again['access_token']}"}, json={"code": code})).status_code == 400
    assert (await client.post(f"/v1/admin/phone-changes/{item['ref_id']}/not-me", headers=boss)).status_code == 409  # already handled


async def test_not_me_after_the_change_puts_the_old_number_back_through_the_products(client, monkeypatch):
    moved = []

    async def hook(product, old, new):
        moved.append((old, new))
        return True

    monkeypatch.setattr(product_hooks, "phone_changed", hook)
    _, h = await signed_in(client)
    boss = await admin_headers("super_admin", "0700000002")
    await _request(client, h)
    code, item = await relay_code(client, boss)
    await client.post("/v1/me/phone/confirm", headers=h, json={"code": code})
    r = await client.post(f"/v1/admin/phone-changes/{item['ref_id']}/not-me", headers=boss)
    assert r.json() == {"result": "reverted", "frozen": True}
    assert moved == [("+2250701020304", "+2250705060708"), ("+2250705060708", "+2250701020304")]
    async with SessionLocal() as db:
        user = await accounts.user_by_phone(db, "+2250701020304")
        assert user is not None and user.status == "disabled"
        assert await accounts.user_by_phone(db, "+2250705060708") is None
    assert (await client.post(f"/v1/admin/users/{user.id}/enable", headers=boss)).status_code == 204
    assert (await client.post("/v1/login", json={"phone": "0701020304", **LOGIN})).status_code == 200
    assert (await client.post("/v1/login", json={"phone": "0705060708", **LOGIN})).status_code == 401


async def test_if_a_product_cannot_move_back_the_freeze_still_holds_and_the_action_can_be_repeated(client, monkeypatch):
    state = {"down": False}

    async def hook(product, old, new):
        if state["down"]:
            raise product_hooks.HookFailed(product)
        return True

    monkeypatch.setattr(product_hooks, "phone_changed", hook)
    _, h = await signed_in(client)
    boss = await admin_headers("super_admin", "0700000002")
    await _request(client, h)
    code, item = await relay_code(client, boss)
    await client.post("/v1/me/phone/confirm", headers=h, json={"code": code})
    state["down"] = True
    r = await client.post(f"/v1/admin/phone-changes/{item['ref_id']}/not-me", headers=boss)
    assert r.status_code == 503
    assert (await client.post("/v1/login", json={"phone": "0705060708", **LOGIN})).json()["detail"] == "account_blocked"  # frozen all the same
    state["down"] = False
    r = await client.post(f"/v1/admin/phone-changes/{item['ref_id']}/not-me", headers=boss)
    assert r.json()["result"] == "reverted"


async def test_only_an_admin_can_say_not_me(client):
    _, h = await signed_in(client)
    await _request(client, h)
    assert (await client.post("/v1/admin/phone-changes/1/not-me", headers=h)).status_code == 403


# --- the security delay (24 h by default) --------------------------------------------------------------------------------


@pytest.fixture
def delayed(monkeypatch):
    monkeypatch.setattr(get_settings(), "phone_change_delay_hours", 24)


async def _scheduled(client, monkeypatch, moved=None):
    async def hook(product, old, new):
        if moved is not None:
            moved.append((old, new))
        return True

    monkeypatch.setattr(product_hooks, "phone_changed", hook)
    t, h = await signed_in(client)
    boss = await admin_headers("super_admin", "0700000002")
    await _request(client, h)
    code, item = await relay_code(client, boss)
    r = await client.post("/v1/me/phone/confirm", headers=h, json={"code": code})
    return t, h, boss, item, r


async def _due(hours=25):
    from datetime import timedelta

    from app import tokens
    from app.models import PhoneChange

    async with SessionLocal() as db:
        for pc in (await db.scalars(select(PhoneChange))).all():
            pc.effective_at = tokens.utcnow() - timedelta(hours=hours - 24)
        await db.commit()


async def test_with_the_delay_the_code_only_schedules_the_change_and_the_old_number_keeps_working(client, monkeypatch, delayed):
    moved = []
    t, h, boss, item, r = await _scheduled(client, monkeypatch, moved)
    assert r.status_code == 200 and r.json()["status"] == "scheduled" and r.json()["effective_at"].endswith("Z")
    assert moved == []  # nothing moves in the products yet
    assert (await client.post("/v1/login", json={"phone": "0701020304", **LOGIN})).status_code == 200
    assert (await client.post("/v1/login", json={"phone": "0705060708", **LOGIN})).status_code == 401
    assert (await client.post("/v1/refresh", json={"refresh_token": t["refresh_token"]})).status_code == 200
    rows = (await client.get("/v1/admin/phone-changes", headers=boss)).json()
    assert [(x["status"], x["new_phone_masked"]) for x in rows] == [("scheduled", "+225 07 •• •• 08")]
    assert _queue((await client.get("/v1/admin/relay-queue", headers=boss)).json(), "phone_changed_notice") == []
    # and the warning to the old number is still good to send
    alert = _queue((await client.get("/v1/admin/relay-queue", headers=boss)).json(), "phone_change_alert")[0]
    assert (await client.post(f"/v1/admin/relay/{alert['id']}/send", headers=boss)).status_code == 200


async def test_when_the_delay_has_passed_the_job_applies_it_and_ends_every_session(client, monkeypatch, delayed):
    moved = []
    t, h, boss, item, r = await _scheduled(client, monkeypatch, moved)
    async with SessionLocal() as db:
        assert await accounts.apply_due_phone_changes(db) == {"applied": 0, "waiting_for_product": 0, "failed": 0}  # not yet
    await _due()
    async with SessionLocal() as db:
        assert await accounts.apply_due_phone_changes(db) == {"applied": 1, "waiting_for_product": 0, "failed": 0}
        await db.commit()
    assert moved == [("+2250701020304", "+2250705060708")]
    assert (await client.post("/v1/refresh", json={"refresh_token": t["refresh_token"]})).status_code == 401
    assert (await client.post("/v1/login", json={"phone": "0705060708", **LOGIN})).status_code == 200
    assert (await client.post("/v1/login", json={"phone": "0701020304", **LOGIN})).status_code == 401
    assert len(_queue((await client.get("/v1/admin/relay-queue", headers=boss)).json(), "phone_changed_notice")) == 1


async def test_signing_in_after_the_delay_applies_it_without_waiting_for_the_job(client, monkeypatch, delayed):
    await _scheduled(client, monkeypatch)
    assert (await client.post("/v1/login", json={"phone": "0705060708", **LOGIN})).status_code == 401  # too early
    await _due()
    assert (await client.post("/v1/login", json={"phone": "0705060708", **LOGIN})).status_code == 200  # on the new number: it applies, then signs in
    assert (await client.post("/v1/login", json={"phone": "0701020304", **LOGIN})).status_code == 401


async def test_signing_in_with_the_old_number_after_the_delay_also_applies_it(client, monkeypatch, delayed):
    await _scheduled(client, monkeypatch)
    await _due()
    assert (await client.post("/v1/login", json={"phone": "0701020304", **LOGIN})).status_code == 401  # the old number is gone
    assert (await client.post("/v1/login", json={"phone": "0705060708", **LOGIN})).status_code == 200


async def test_a_product_that_is_down_keeps_the_change_scheduled_for_the_next_run(client, monkeypatch, delayed):
    await _scheduled(client, monkeypatch)
    state = {"down": True}

    async def hook(product, old, new):
        if state["down"]:
            raise product_hooks.HookFailed(product)
        return True

    monkeypatch.setattr(product_hooks, "phone_changed", hook)
    await _due()
    async with SessionLocal() as db:
        assert (await accounts.apply_due_phone_changes(db))["waiting_for_product"] == 1
        await db.commit()
    assert (await client.post("/v1/login", json={"phone": "0701020304", **LOGIN})).status_code == 200  # still her number, nothing half done
    state["down"] = False
    async with SessionLocal() as db:
        assert (await accounts.apply_due_phone_changes(db))["applied"] == 1
        await db.commit()


async def test_a_number_taken_during_the_delay_fails_the_change_cleanly(client, monkeypatch, delayed):
    await _scheduled(client, monkeypatch)
    await make_user("0705060708", role="customer", product="fidelia")
    await _due()
    async with SessionLocal() as db:
        assert (await accounts.apply_due_phone_changes(db))["failed"] == 1
        await db.commit()
    assert (await client.post("/v1/login", json={"phone": "0701020304", **LOGIN})).status_code == 200


async def test_an_admin_who_called_her_can_make_it_effective_at_once_with_a_written_note(client, monkeypatch, delayed):
    moved = []
    t, h, boss, item, r = await _scheduled(client, monkeypatch, moved)
    pc_id = item["ref_id"]
    short = await client.post(f"/v1/admin/phone-changes/{pc_id}/activate-now", headers=boss, json={"note": "ok"})
    assert short.status_code == 422 and moved == []
    done = await client.post(f"/v1/admin/phone-changes/{pc_id}/activate-now", headers=boss, json={"note": "Appelée sur son ancien numéro, confirme"})
    assert done.status_code == 200 and done.json()["status"] == "done"
    assert moved == [("+2250701020304", "+2250705060708")]
    assert (await client.post("/v1/login", json={"phone": "0705060708", **LOGIN})).status_code == 200
    again = await client.post(f"/v1/admin/phone-changes/{pc_id}/activate-now", headers=boss, json={"note": "Appelée sur son ancien numéro, confirme"})
    assert again.status_code == 409
    async with SessionLocal() as db:
        audit = (await db.scalars(select(AuditLog).where(AuditLog.action == "user.phone_changed"))).all()
        assert audit[-1].actor_type == "admin" and "Appelée" in audit[-1].detail["note"]


async def test_only_an_admin_can_activate_now(client, monkeypatch, delayed):
    t, h, boss, item, r = await _scheduled(client, monkeypatch)
    assert (await client.post(f"/v1/admin/phone-changes/{item['ref_id']}/activate-now", headers=h, json={"note": "x" * 12})).status_code == 403


async def test_not_me_during_the_delay_cancels_it_and_freezes(client, monkeypatch, delayed):
    t, h, boss, item, r = await _scheduled(client, monkeypatch)
    out = await client.post(f"/v1/admin/phone-changes/{item['ref_id']}/not-me", headers=boss)
    assert out.json() == {"result": "cancelled", "frozen": True}
    await _due()
    async with SessionLocal() as db:
        assert (await accounts.apply_due_phone_changes(db))["applied"] == 0  # cancelled means cancelled
    assert (await client.post("/v1/login", json={"phone": "0701020304", **LOGIN})).json()["detail"] == "account_blocked"
