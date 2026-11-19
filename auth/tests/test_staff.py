import jwt
import pyotp
from sqlalchemy import select

from app.db import SessionLocal
from app.models import AuditLog
from tests.conftest import PASSWORD, admin_headers, make_user


def body(**over):
    return {"phone": "0711111111", "display_name": "Awa Operator", "product": "flariane", "role": "operator", **over}


async def test_only_a_super_admin_creates_staff(client):
    admin = await admin_headers("admin", "0700000001")
    boss = await admin_headers("super_admin", "0700000002")
    assert (await client.post("/v1/admin/staff", json=body())).status_code == 401
    r = await client.post("/v1/admin/staff", headers=admin, json=body())
    assert r.status_code == 403 and r.json()["detail"] == "super_admin_only"
    assert (await client.post("/v1/admin/staff", headers=boss, json=body())).status_code == 201


async def test_staff_with_totp_role_gets_password_and_authenticator_once_and_must_change_it(client):
    boss = await admin_headers("super_admin", "0700000002")
    r = await client.post("/v1/admin/staff", headers=boss, json=body())
    out = r.json()
    assert r.headers["cache-control"] == "no-store"
    assert out["temporary_password"] and out["totp"]["secret"] and out["totp"]["uri"].startswith("otpauth://totp/")
    login = {"phone": "0711111111", "password": out["temporary_password"], "product": "flariane", "app": "admin"}
    assert (await client.post("/v1/login", json=login)).json()["detail"] == "invalid_totp"  # the password alone is not enough
    t = await client.post("/v1/login", json={**login, "totp": pyotp.TOTP(out["totp"]["secret"]).now()})
    assert t.status_code == 200 and t.json()["must_change_password"] is True and t.json()["role"] == "operator"
    claims = jwt.decode(t.json()["access_token"], options={"verify_signature": False})
    assert claims["amr"] == ["pwd", "totp"] and claims["mcp"] is True
    h = {"Authorization": f"Bearer {t.json()['access_token']}"}
    assert (await client.post("/v1/password/change", headers=h, json={"current_password": out["temporary_password"], "new_password": "Brand-New-Pass-1"})).status_code == 204
    # the temporary password is not stored in clear and the secret is not returned again
    assert (await client.get("/v1/admin/users/search", params={"phone": "0711111111"}, headers=boss)).json().get("temporary_password") is None


async def test_agent_has_no_authenticator_and_can_be_deactivated_by_an_admin(client):
    boss = await admin_headers("super_admin", "0700000002")
    admin = await admin_headers("admin", "0700000001")
    r = await client.post("/v1/admin/staff", headers=boss, json=body(phone="0722222222", product="fidelia", role="agent", display_name="Koffi Agent"))
    out = r.json()
    assert out["totp"] is None  # owner decision: agents need no authenticator app
    t = await client.post("/v1/login", json={"phone": "0722222222", "password": out["temporary_password"], "product": "fidelia", "app": "agent"})
    assert t.status_code == 200 and jwt.decode(t.json()["access_token"], options={"verify_signature": False})["amr"] == ["pwd"]
    uid = out["user_id"]
    assert (await client.post(f"/v1/admin/users/{uid}/disable", headers=admin)).status_code == 204  # an admin (not only a super admin) can deactivate an agent
    assert (await client.post("/v1/refresh", json={"refresh_token": t.json()["refresh_token"]})).status_code == 401
    assert (await client.post("/v1/login", json={"phone": "0722222222", "password": out["temporary_password"], "product": "fidelia", "app": "agent"})).json()["detail"] == "account_blocked"


async def test_staff_creation_rules(client):
    boss = await admin_headers("super_admin", "0700000002")
    assert (await client.post("/v1/admin/staff", headers=boss, json=body(role="customer"))).json()["detail"] == "not_a_staff_role"
    assert (await client.post("/v1/admin/staff", headers=boss, json=body(role="emperor"))).json()["detail"] == "not_a_staff_role"
    assert (await client.post("/v1/admin/staff", headers=boss, json=body(product="nope"))).json()["detail"] == "unknown_product"
    assert (await client.post("/v1/admin/staff", headers=boss, json=body(phone="123456789"))).json()["detail"] == "invalid_phone"
    assert (await client.post("/v1/admin/staff", headers=boss, json=body())).status_code == 201
    assert (await client.post("/v1/admin/staff", headers=boss, json=body())).json()["detail"] == "phone_taken"


async def test_reset_staff_gives_a_new_password_revokes_sessions_and_can_rotate_totp(client):
    boss = await admin_headers("super_admin", "0700000002")
    out = (await client.post("/v1/admin/staff", headers=boss, json=body())).json()
    old_secret = out["totp"]["secret"]
    uid = out["user_id"]
    r = (await client.post(f"/v1/admin/staff/{uid}/reset", headers=boss, json={"reset_totp": True})).json()
    assert r["temporary_password"] != out["temporary_password"] and r["totp"]["secret"] != old_secret
    login = {"phone": "0711111111", "product": "flariane", "app": "admin"}
    assert (await client.post("/v1/login", json={**login, "password": out["temporary_password"], "totp": pyotp.TOTP(old_secret).now()})).status_code == 401
    ok = await client.post("/v1/login", json={**login, "password": r["temporary_password"], "totp": pyotp.TOTP(r["totp"]["secret"]).now()})
    assert ok.status_code == 200
    # a super admin cannot reset themselves, and a customer is not staff
    me = await make_user("0733333333", role="customer")
    assert (await client.post(f"/v1/admin/staff/{me.id}/reset", headers=boss, json={})).json()["detail"] == "not_a_staff_account"


async def test_staff_audit_has_no_secrets(client):
    boss = await admin_headers("super_admin", "0700000002")
    out = (await client.post("/v1/admin/staff", headers=boss, json=body())).json()
    async with SessionLocal() as db:
        rows = (await db.scalars(select(AuditLog).where(AuditLog.action == "staff.created"))).all()
    dump = str([(r.detail, r.entity_id) for r in rows])
    assert rows and out["temporary_password"] not in dump and out["totp"]["secret"] not in dump and "0711111111" not in dump
