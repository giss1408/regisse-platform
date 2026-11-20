"""LOCAL CHAIN RUN for the supermarket on the platform: real auth + the real supermarket backend (Postgres, Redis, Caddy), over HTTP. People sign in
at auth (phone + password, WhatsApp activation, authenticator code for the platform admin); the supermarket only verifies tokens."""
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
smk = httpx.Client(base_url=f"http://supermarket-api.{D}", timeout=30)
ok = fail = 0
PW = "Market-Pass-2026x"


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


def next_totp(secret, last_step):
    while int(time.time()) // 30 <= last_step:
        time.sleep(1)
    step = int(time.time()) // 30
    return pyotp.TOTP(secret).at(step * 30), step


def login(ph, pw, app, **extra):
    r = auth.post("/v1/login", json={"phone": ph, "password": pw, "product": "supermarket", "app": app, **extra})
    return r.status_code, r.json()


def pending(h, purpose, tail):
    rows = [x for x in auth.get("/v1/admin/relay-queue", params={"status": "pending"}, headers=h).json() if x["purpose"] == purpose and x["phone_masked"].endswith(tail)]
    return rows[-1]


# --- a platform admin is created at auth by a super admin (in person: temporary password + authenticator), then linked here
b = {"phone": os.environ["E2E_ADMIN_PHONE"], "password": os.environ["E2E_ADMIN_PASSWORD"], "product": "auth", "app": "admin"}
code, bstep = next_totp(os.environ["E2E_ADMIN_TOTP_SECRET"], 0)
r = auth.post("/v1/login", json={**b, "totp": code})
if r.status_code == 401:
    time.sleep(31)
    code, bstep = next_totp(os.environ["E2E_ADMIN_TOTP_SECRET"], bstep)
    r = auth.post("/v1/login", json={**b, "totp": code})
boss = H(r.json()["access_token"])
admin_ph = rnd()
made = auth.post("/v1/admin/staff", headers=boss, json={"phone": admin_ph, "display_name": "Admin Marche", "product": "supermarket", "role": "platform_admin"}).json()
open("/state/admin_id", "w").write(made["user_id"])
secret, temp = made["totp"]["secret"], made["temporary_password"]
check("a super admin creates the supermarket's platform admin at auth (temporary password + authenticator secret)", bool(secret and temp), made)
code, astep = next_totp(secret, 0)
s, t = login(admin_ph, temp, "admin", totp=code)
check("she signs in with password + authenticator and must choose a password", s == 200 and t["must_change_password"], t)
auth.post("/v1/password/change", headers=H(t["access_token"]), json={"current_password": temp, "new_password": PW})
t = auth.post("/v1/refresh", json={"refresh_token": t["refresh_token"]}).json()
adm = H(t["access_token"])
r = smk.get("/api/v1/me", headers=adm)
check("before she is linked here, the supermarket refuses her token (staff are never created from a token)", r.status_code == 401, r.text)
print("LINK_ADMIN", made["user_id"], flush=True)
sys.stdout.flush()
open("/state/admin_ph", "w").write(admin_ph)
open("/state/admin_totp", "w").write(f"{secret} {astep}")
open("/state/adm_token", "w").write(t["access_token"])
print(f"\n{ok} passed, {fail} failed (part 1)")
sys.exit(1 if fail else 0)
