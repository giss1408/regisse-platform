"""SMOKE (not e2e): a customer created in `auth` can call flariane with the auth token; a forged token and an admin route
are refused. Needs infra, auth and flariane (FLR_SSO_ENABLED=true) running."""
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
flr = httpx.Client(base_url=f"http://flariane-api.{d}", timeout=30)
ok = fail = 0


def check(name, cond, extra=""):
    global ok, fail
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "  -> " + str(extra)))
    ok += bool(cond)
    fail += not cond


def totp_login():
    body = {"phone": os.environ["E2E_ADMIN_PHONE"], "password": os.environ["E2E_ADMIN_PASSWORD"], "product": "auth", "app": "admin"}
    return auth.post("/v1/login", json={**body, "totp": pyotp.TOTP(os.environ["E2E_ADMIN_TOTP_SECRET"]).now()})


r = totp_login()
if r.status_code == 401:  # this 30 s code was already used by another run
    time.sleep(31)
    r = totp_login()
admin = {"Authorization": "Bearer " + r.json()["access_token"]}
phone = "07%08d" % random.randint(10000000, 99999999)
pw = "Smoke-Secret-2026"
auth.post("/v1/register", json={"phone": phone, "password": pw, "display_name": "Smoke Awa", "product": "flariane", "consent": True})
item = next(i for i in auth.get("/v1/admin/relay-queue", headers=admin).json() if i["phone_masked"].endswith(phone[-2:]) and i["purpose"] == "activation")
url = auth.post(f"/v1/admin/relay/{item['id']}/send", headers=admin).json()["url"]
code = re.search(r"\*(\d{6})\*", parse_qs(urlsplit(url).query)["text"][0]).group(1)
auth.post("/v1/activate", json={"phone": phone, "code": code})
t = auth.post("/v1/login", json={"phone": phone, "password": pw, "product": "flariane", "app": "customer"}).json()["access_token"]
h = {"Authorization": "Bearer " + t}
r = flr.get("/me", headers=h)
check("flariane accepts the auth token on a customer route (customer created on first use)", r.status_code == 200, r.text[:200])
check("second call returns the same customer", flr.get("/me", headers=h).json().get("id") == r.json().get("id"))
check("auth token is refused on admin routes", flr.get("/admin/orders", headers=h).status_code == 401)
check("forged token is refused", flr.get("/me", headers={"Authorization": "Bearer " + t[:-6] + "AAAAAA"}).status_code == 401)
check("anonymous is refused", flr.get("/me").status_code == 401)
print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
