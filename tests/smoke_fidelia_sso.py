"""SMOKE (not e2e): the real fidelia-BE (SQLite, SSO on) accepts tokens from the real auth service through the platform network."""
import os
import random
import re
import sys
import time
from urllib.parse import parse_qs, urlsplit

import httpx
import pyotp

d = os.environ["DOMAIN"]
auth = httpx.Client(base_url=f"http://auth-api.{d}", timeout=30)
fid = httpx.Client(base_url="http://fidelia-smoke:8000", timeout=30)
ok = fail = 0


def check(name, cond, extra=""):
    global ok, fail
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "  -> " + str(extra)[:240]))
    ok += bool(cond)
    fail += not cond


body = {"phone": os.environ["E2E_ADMIN_PHONE"], "password": os.environ["E2E_ADMIN_PASSWORD"], "product": "auth", "app": "admin"}
r = auth.post("/v1/login", json={**body, "totp": pyotp.TOTP(os.environ["E2E_ADMIN_TOTP_SECRET"]).now()})
if r.status_code == 401:
    time.sleep(31)
    r = auth.post("/v1/login", json={**body, "totp": pyotp.TOTP(os.environ["E2E_ADMIN_TOTP_SECRET"]).now()})
boss = {"Authorization": "Bearer " + r.json()["access_token"]}


def make_user(name):
    ph, pw = "07%08d" % random.randint(10000000, 99999999), "Fid-Secret-2026"
    auth.post("/v1/register", json={"phone": ph, "password": pw, "display_name": name, "product": "fidelia", "consent": True})
    q = auth.get("/v1/admin/relay-queue", headers=boss).json()
    item = next(i for i in q if i["purpose"] == "activation" and i["phone_masked"].endswith(ph[-2:]) and i["product"] == "fidelia")
    text = parse_qs(urlsplit(auth.post(f"/v1/admin/relay/{item['id']}/send", headers=boss).json()["url"]).query)["text"][0]
    auth.post("/v1/activate", json={"phone": ph, "code": re.search(r"\*(\d{6})\*", text).group(1)})
    return ph, pw


def login(ph, pw, app):
    r = auth.post("/v1/login", json={"phone": ph, "password": pw, "product": "fidelia", "app": app})
    return r.json()


# --- a customer
c_ph, c_pw = make_user("Fid Client")
ct = login(c_ph, c_pw, "customer")
check("auth issues a fidelia token (aud fidelia, role customer)", ct.get("role") == "customer", ct)
r = fid.get("/api/auth/me", headers={"Authorization": "Bearer " + ct["access_token"]})
check("fidelia accepts it and answers with the phone key identity", r.status_code == 200 and r.json()["sub"] == f"tel:+225{c_ph}" and r.json()["role"] == "customer", r.text)
check("a tampered token is refused", fid.get("/api/auth/me", headers={"Authorization": "Bearer " + ct["access_token"][:-6] + "AAAAAA"}).status_code == 401)
# (audience mix-ups are covered by the unit tests: a user cannot even get a token for a product she has no role in)

# --- a merchant: role granted in auth, shop enrolled in fidelia by its own admin (legacy token)
m_ph, m_pw = make_user("Fid Marchand")
uid = auth.get("/v1/admin/users/search", params={"phone": m_ph}, headers=boss).json()["id"]
check("auth super admin grants the merchant role", auth.post("/v1/admin/memberships", headers=boss, json={"user_id": uid, "product": "fidelia", "role": "merchant"}).status_code == 201)
legacy_admin = {"Authorization": "Bearer " + fid.post("/api/token", data={"username": "admin", "password": "admin123"}).json()["access_token"]}
venue = fid.post("/api/admin/venues", headers=legacy_admin, json={"category": "maquis", "name": "Maquis SSO", "commune": "Cocody", "merchant_phone": m_ph, "payout_provider": "wave", "payout_account": m_ph})
check("fidelia enrols the shop for that phone (its own admin, as today)", venue.status_code == 201, venue.text)
mt = login(m_ph, m_pw, "merchant")
mh = {"Authorization": "Bearer " + mt["access_token"]}
check("the merchant token finds the shop by phone key", fid.get("/api/merchant/pay-code", headers=mh).json().get("pay_code") == venue.json()["pay_code"], fid.get("/api/merchant/pay-code", headers=mh).text)
r = fid.put("/api/merchant/wave", headers=mh, json={"api_key": "wave_ci_prod_" + "k" * 40, "webhook_secret": "wave_ci_WHS_" + "s" * 30})
check("wallet route: fresh proof + live session pass the guard (the handler then judges the Wave key)", r.json().get("detail") not in ("step_up_required", "sso_session_ended", "sso_unavailable"), r.text)

# step-up through auth gives another fresh token for the same session
su = auth.post("/v1/step-up", headers=mh, json={"password": m_pw})
check("auth step-up returns a fresh token", su.status_code == 200 and su.json()["amr"] == ["pwd"], su.text)
r = fid.put("/api/merchant/wave", headers={"Authorization": "Bearer " + su.json()["access_token"]}, json={"api_key": "wave_ci_prod_" + "k" * 40, "webhook_secret": "wave_ci_WHS_" + "s" * 30})
check("fidelia accepts the stepped-up token on the wallet route", r.json().get("detail") not in ("step_up_required", "sso_session_ended", "sso_unavailable"), r.text)

# end the session at auth: the token is still valid for 10 minutes, but money routes check live
auth.post("/v1/logout", json={"refresh_token": mt["refresh_token"]})
r = fid.put("/api/merchant/wave", headers=mh, json={"api_key": "wave_ci_prod_" + "k" * 40, "webhook_secret": "wave_ci_WHS_" + "s" * 30})
check("after logout at auth, the wallet route refuses the still-unexpired token", r.status_code == 401 and r.json()["detail"] == "sso_session_ended", r.text)
check("...while an ordinary route still works until the token expires (by design: signatures verified locally)", fid.get("/api/merchant/pay-code", headers=mh).status_code == 200)
print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
