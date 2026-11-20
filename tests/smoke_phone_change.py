"""SMOKE against the running auth service: sign up, team relay, activate, then the number change (password, code to the NEW number,
confirm, session renewed, other session ended, old number out, new number in)."""
import os, random, re, sys
from urllib.parse import parse_qs, urlsplit

import httpx
import pyotp

D, PORT = os.environ["DOMAIN"], os.environ["PORT"]
c = httpx.Client(base_url=f"http://auth-api.{D}", timeout=20)
ok = bad = 0


def check(name, cond, extra=""):
    global ok, bad
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"  {extra}" if not cond else ""))
    ok, bad = ok + cond, bad + (not cond)


def code_of(url):
    return re.search(r"\*(\d{6})\*", parse_qs(urlsplit(url).query)["text"][0]).group(1)


def n():
    return "07" + "".join(random.choice("0123456789") for _ in range(8))


adm = c.post("/v1/login", json={"phone": os.environ["E2E_ADMIN_PHONE"], "password": os.environ["E2E_ADMIN_PASSWORD"], "product": "fidelia", "app": "admin",
                                 "totp": pyotp.TOTP(os.environ["E2E_ADMIN_TOTP_SECRET"]).now()})
if adm.status_code != 200:  # the e2e admin may only be a member of other products: use the auth back office space
    adm = c.post("/v1/login", json={"phone": os.environ["E2E_ADMIN_PHONE"], "password": os.environ["E2E_ADMIN_PASSWORD"], "product": "auth", "app": "admin",
                                     "totp": pyotp.TOTP(os.environ["E2E_ADMIN_TOTP_SECRET"]).now()})
H = {"Authorization": f"Bearer {adm.json()['access_token']}"}
old, new, pw = n(), n(), "Phone-Change-2026x"


def relay(purpose):
    q = [x for x in c.get("/v1/admin/relay-queue", headers=H).json() if x["purpose"] == purpose]
    return code_of(c.post(f"/v1/admin/relay/{q[-1]['id']}/send", headers=H).json()["url"])


c.post("/v1/register", json={"phone": old, "password": pw, "product": "fidelia", "consent": True})
c.post("/v1/activate", json={"phone": old, "code": relay("activation")})
L = {"product": "fidelia", "app": "customer"}
a = c.post("/v1/login", json={"phone": old, "password": pw, **L}).json()
b = c.post("/v1/login", json={"phone": old, "password": pw, **L}).json()
ha = {"Authorization": f"Bearer {a['access_token']}"}
check("signed up, activated, signed in on two devices", "access_token" in a and "access_token" in b)
bad_pw = c.post("/v1/me/phone/request", headers=ha, json={"new_phone": new, "current_password": "wrong-password-1"})
check("a wrong password is refused", bad_pw.status_code == 401)
r = c.post("/v1/me/phone/request", headers=ha, json={"new_phone": new, "current_password": pw})
check("request accepted, new number masked", r.status_code == 202 and r.json()["new_phone_masked"].endswith(new[-2:]))
rows = c.get("/v1/admin/relay-queue", headers=H).json()
alert = [x for x in rows if x["purpose"] == "phone_change_alert"][-1]
aurl = c.post(f"/v1/admin/relay/{alert['id']}/send", headers=H).json()["url"]
check("the OLD number is warned at once (link to the old number, no code in it)", f"225{old}" in aurl and f"225{new}" not in aurl and "PAS MOI" in aurl.replace("%20", " ") or "PAS%20MOI" in aurl)
q = [x for x in rows if x["purpose"] == "phone_change"][-1]
url = c.post(f"/v1/admin/relay/{q['id']}/send", headers=H).json()["url"]
check("the WhatsApp link goes to the NEW number", f"225{new}" in url and f"225{old}" not in url)
code = code_of(url)
check("a wrong code changes nothing", c.post("/v1/me/phone/confirm", headers=ha, json={"code": "000000"}).status_code == 400)
check("the right code confirms", c.post("/v1/me/phone/confirm", headers=ha, json={"code": code}).status_code == 200)
check("the other device is signed out", c.post("/v1/refresh", json={"refresh_token": b["refresh_token"]}).status_code == 401)
rn = c.post("/v1/refresh", json={"refresh_token": a["refresh_token"]})
import base64, json
tok = rn.json().get("access_token", "..")
ph = json.loads(base64.urlsafe_b64decode(tok.split(".")[1] + "=" * (-len(tok.split(".")[1]) % 4))).get("ph") if rn.status_code == 200 else None
check("this device renews and carries the new number", ph == f"tel:+225{new}", ph)
check("the old number no longer signs in", c.post("/v1/login", json={"phone": old, "password": pw, **L}).status_code == 401)
check("the new number signs in with the same password", c.post("/v1/login", json={"phone": new, "password": pw, **L}).status_code == 200)
check("the code cannot be used twice", c.post("/v1/me/phone/confirm", headers=ha, json={"code": code}).status_code == 400)
# after the change: the old number gets a notice, and "this wasn't me" puts it back and freezes
rows = c.get("/v1/admin/relay-queue", headers=H).json()
notice = [x for x in rows if x["purpose"] == "phone_changed_notice"][-1]
nurl = c.post(f"/v1/admin/relay/{notice['id']}/send", headers=H).json()["url"]
check("a notice for the old number after the change", f"225{old}" in nurl)
nm = c.post(f"/v1/admin/phone-changes/{notice['ref_id']}/not-me", headers=H)
check("'this wasn't me' reverts the number and freezes the account", nm.status_code == 200 and nm.json() == {"result": "reverted", "frozen": True}, nm.text)
check("frozen: neither number signs in", c.post("/v1/login", json={"phone": old, "password": pw, **L}).json().get("detail") == "account_blocked"
      and c.post("/v1/login", json={"phone": new, "password": pw, **L}).status_code == 401)
uid = c.get("/v1/admin/users/search", params={"phone": old}, headers=H).json()["id"]
check("an admin re-enables after the call", c.post(f"/v1/admin/users/{uid}/enable", headers=H).status_code == 204)
check("the OLD number signs in again", c.post("/v1/login", json={"phone": old, "password": pw, **L}).status_code == 200)
print(f"\nresult: {ok} passed, {bad} failed")
sys.exit(1 if bad else 0)
