"""SMOKE (not e2e): a flariane back-office user imported into auth signs in at auth, exchanges the token at flariane
(device approval stays in flariane), and loses access when auth ends her session."""
import json
import os
import sys
import time

import httpx
import pyotp

d = os.environ["DOMAIN"]
auth = httpx.Client(base_url=f"http://auth-api.{d}", timeout=30)
flr = httpx.Client(base_url=f"http://flariane-api.{d}", timeout=30)
ok = fail = 0


def check(name, cond, extra=""):
    global ok, fail
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "  -> " + str(extra)[:200]))
    ok += bool(cond)
    fail += not cond


def totp_login(client, body, secret):
    r = client.post("/v1/login", json={**body, "totp": pyotp.TOTP(secret).now()})
    if r.status_code == 401 and r.json().get("detail") == "invalid_totp":  # this 30 s code was used by a previous run
        time.sleep(31)
        r = client.post("/v1/login", json={**body, "totp": pyotp.TOTP(secret).now()})
    return r


# the back-office super admin of auth (to disable the imported user later)
boss = totp_login(auth, {"phone": os.environ["E2E_ADMIN_PHONE"], "password": os.environ["E2E_ADMIN_PASSWORD"], "product": "auth", "app": "admin"}, os.environ["E2E_ADMIN_TOTP_SECRET"])
boss_h = {"Authorization": "Bearer " + boss.json()["access_token"]}

email, pw, secret = "e2e@flariane.test", os.environ["FLR_E2E_PASSWORD"], os.environ["FLR_E2E_TOTP"]
dev = json.loads(os.environ["FLR_E2E_DEVICE"])  # an APPROVED flariane device of this admin (from the flariane e2e state)
r = totp_login(auth, {"email": email, "password": pw, "product": "flariane", "app": "admin"}, secret)
check("imported admin signs in at auth with her email, password and her EXISTING authenticator code", r.status_code == 200, r.text)
t = r.json()
claims = json.loads(__import__("base64").urlsafe_b64decode(t["access_token"].split(".")[1] + "=="))
check("token: role super_admin, amr pwd+totp, lid = her flariane admin id, no phone", claims["role"] == "super_admin" and claims["amr"] == ["pwd", "totp"] and claims.get("lid", "").isdigit() and claims["ph"] == "", claims)

ex = flr.post("/admin/auth/sso-login", json={"device": {"name": "E2E runner", "platform": "android", **dev}}, headers={"Authorization": "Bearer " + t["access_token"]})
check("flariane exchanges the auth token for a back-office session on her approved device", ex.status_code == 200 and ex.json()["status"] == "ok", ex.text)
fh = {"Authorization": "Bearer " + ex.json()["tokens"]["access_token"]}
check("the flariane session opens admin routes", flr.get("/admin/orders", headers=fh).status_code == 200)
check("it is her own admin record", flr.get("/admin/auth/me", headers=fh).json()["email"] == email)
check("the auth token itself is not accepted on admin routes", flr.get("/admin/orders", headers={"Authorization": "Bearer " + t["access_token"]}).status_code == 401)
new_dev = flr.post("/admin/auth/sso-login", json={"device": {"name": "Unknown phone", "platform": "ios"}}, headers={"Authorization": "Bearer " + t["access_token"]})
check("an unknown device is NOT approved by signing in at auth: it waits for a super admin", new_dev.status_code == 200 and new_dev.json()["status"] == "device_pending" and not new_dev.json().get("tokens"), new_dev.text)

rt = ex.json()["tokens"]["refresh_token"]
rf = flr.post("/admin/auth/refresh", json={"refresh_token": rt, "device_id": dev["device_id"], "device_secret": dev["device_secret"]})
check("refresh works while the auth session is alive", rf.status_code == 200, rf.text)

uid = auth.get("/v1/admin/users/search", params={"email": email}, headers=boss_h).json()["id"]
check("auth super admin disables the user", auth.post(f"/v1/admin/users/{uid}/disable", headers=boss_h).status_code == 204)
rf2 = flr.post("/admin/auth/refresh", json={"refresh_token": rf.json()["refresh_token"], "device_id": dev["device_id"], "device_secret": dev["device_secret"]})
check("flariane ends her back-office session at the next refresh", rf2.status_code == 401 and rf2.json()["detail"] == "sso_session_ended", rf2.text)
check("she cannot sign in at auth any more", auth.post("/v1/login", json={"email": email, "password": pw, "product": "flariane", "app": "admin", "totp": pyotp.TOTP(secret).at(time.time() + 30)}).json().get("detail") == "account_blocked")
check("auth super admin re-enables her (cleanup)", auth.post(f"/v1/admin/users/{uid}/enable", headers=boss_h).status_code == 204)
print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
