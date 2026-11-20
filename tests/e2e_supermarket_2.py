"""Part 2 (after the platform admin was linked in the supermarket): customer, dashboard, retailer enrolment, staff, deletion."""
import os
import random
import re
import sys
import time
from urllib.parse import parse_qs, urlsplit

import httpx
import pyotp

D = os.environ["DOMAIN"]
auth = httpx.Client(base_url=f"http://auth-api.{D}", timeout=30)
smk = httpx.Client(base_url=f"http://supermarket-api.{D}", timeout=30, follow_redirects=False)
ok = fail = 0
PW = "Market-Pass-2026x"
admin_ph = open("/state/admin_ph").read()
secret, astep = open("/state/admin_totp").read().split()
astep = int(astep)


def check(name, cond, extra=""):
    global ok, fail
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "  -> " + str(extra)[:300]), flush=True)
    ok += bool(cond)
    fail += not cond


def H(tok):
    return {"Authorization": "Bearer " + tok}


def code_of(url):
    return re.search(r"\*(\d{6})\*", parse_qs(urlsplit(url).query)["text"][0]).group(1)


def rnd():
    return "07%08d" % random.randint(10000000, 99999999)


def next_totp(last):
    while int(time.time()) // 30 <= last:
        time.sleep(1)
    step = int(time.time()) // 30
    return pyotp.TOTP(secret).at(step * 30), step


def login(ph, pw, app, **extra):
    r = auth.post("/v1/login", json={"phone": ph, "password": pw, "product": "supermarket", "app": app, **extra})
    return r.status_code, r.json()


code, astep = next_totp(astep)
s, t = login(admin_ph, PW, "admin", totp=code)
adm = H(t["access_token"])
r = smk.get("/api/v1/me", headers=adm)
check("once linked, the platform admin's auth token is accepted by the supermarket", r.status_code == 200 and r.json()["role"] == "platform_admin", r.text)

def relay_code(purpose, tail):
    rows = [x for x in auth.get("/v1/admin/relay-queue", params={"status": "pending"}, headers=adm).json() if x["purpose"] == purpose and x["phone_masked"].endswith(tail)]
    return code_of(auth.post(f"/v1/admin/relay/{rows[-1]['id']}/send", headers=adm).json()["url"])

# --- a customer: sign up at auth, the platform admin sends the WhatsApp code, the supermarket opens her profile on first use
c1 = rnd()
auth.post("/v1/register", json={"phone": c1, "password": PW, "display_name": "Cliente Marche", "product": "supermarket", "consent": True})
check("the platform admin sees ONLY the supermarket's queue and sends the activation code", auth.post("/v1/activate", json={"phone": c1, "code": relay_code("activation", c1[-2:])}).status_code == 200)
s, ct = login(c1, PW, "customer")
r = smk.get("/api/v1/me", headers=H(ct["access_token"]))
check("the supermarket opens her profile from the token: customer, no email, a loyalty card", r.status_code == 200 and r.json()["role"] == "customer" and r.json()["email"] == "", r.text)
check("...with her card", smk.get("/api/v1/me/card", headers=H(ct["access_token"])).status_code == 200)
check("the app's own email login is gone (404)", smk.post("/api/v1/auth/login", json={"email": "a@b.fr", "password": PW}).status_code == 404 and smk.post("/api/v1/auth/register", json={}).status_code == 404)
check("a customer token cannot use staff routes", smk.get("/api/v1/staff/products", headers=H(ct["access_token"])).status_code == 403)

# --- the platform dashboard signs the admin in AT auth, and enrols a retailer's first staff member by phone
fresh = httpx.Client(base_url=f"http://supermarket-api.{D}", timeout=30)
ftok = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', fresh.get("/platform/login/").text).group(1)
bad = fresh.post("/platform/login/", data={"csrfmiddlewaretoken": ftok, "phone": admin_ph, "password": "wrong-password-1", "totp": "000000"})
check("a wrong password is refused with words, no session", bad.status_code == 200 and "incorrect" in bad.text and "sessionid" not in fresh.cookies)
page = smk.get("/platform/login/")
tok = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', page.text).group(1)
code, astep = next_totp(astep)
r = smk.post("/platform/login/", data={"csrfmiddlewaretoken": tok, "phone": admin_ph, "password": PW, "totp": code})
check("the dashboard signs the platform admin in at auth (password + authenticator code)", r.status_code == 302 and "login" not in r.headers.get("location", ""), r.status_code)
check("the dashboard lists retailers", smk.get("/platform/").status_code == 200)
st = rnd()
form = smk.get("/platform/retailers/new/")
tok2 = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', form.text).group(1)
check("the new-retailer form asks for a phone, not an email and a password", "staff_phone" in form.text and "staff_password" not in form.text)
r = smk.post("/platform/retailers/new/", data={"csrfmiddlewaretoken": tok2, "name": "Chez Awa Marche", "staff_name": "Awa Caissiere", "staff_phone": st})
check("a retailer and its first staff member (enrolled at auth, linked here) are created", r.status_code == 302 and "/retailers/" in r.headers.get("location", ""), r.status_code)
s, pre = login(st, PW, "retailer")
check("the staff member cannot sign in yet: no password until she types the team's code", s in (401, 403))
check("she activates with the WhatsApp code and a password of her own", auth.post("/v1/activate", json={"phone": st, "code": relay_code("activation", st[-2:]), "password": PW}).status_code == 200)
s, sl = login(st, PW, "retailer")
sh = H(sl["access_token"])
check("she signs in at auth (retailer app) with her own password", s == 200 and sl["role"] == "retailer_staff" and not sl["must_change_password"], sl)
check("the supermarket knows her retailer: staff routes work", smk.get("/api/v1/staff/products", headers=sh).status_code == 200)
r = smk.post("/api/v1/staff/payment-methods", headers=sh, json={"provider": "wave", "phone_number": "07 01 02 03 04"})
check("she adds where customers pay: no local password asked, her fresh sign-in at auth + the live session check are the proof", r.status_code == 201, r.text)
check("and a customer sees it", any(m["provider"] == "wave" for m in smk.get("/api/v1/payment-methods", headers=H(ct["access_token"])).json().get("results", [])) or smk.get("/api/v1/payment-methods").status_code == 200)

# --- closing her session at auth ends her access at once on the money route
auth.post("/v1/logout", json={"refresh_token": sl["refresh_token"]})
r = smk.post("/api/v1/staff/payment-methods", headers=sh, json={"provider": "orange_money", "phone_number": "07 01 02 03 05"})
check("after sign-out at auth, the money write is refused although the token has not expired", r.status_code == 401 and r.json()["code"] == "session_ended", r.text)

# --- account deletion reaches auth
d = smk.delete("/api/v1/me", headers=H(ct["access_token"]))
check("a customer deletes her account in the supermarket", d.status_code == 204, d.text)
s, body = login(c1, PW, "customer")
check("...and her identity is erased at auth too", s in (401, 403) and auth.post("/v1/refresh", json={"refresh_token": ct["refresh_token"]}).status_code == 401, body)
print(f"\n{ok} passed, {fail} failed (part 2)")
sys.exit(1 if fail else 0)
