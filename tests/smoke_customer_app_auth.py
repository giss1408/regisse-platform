"""Prepares users for the customer-app live smoke (Dart): prints the JSON config for --dart-define=LIVE_CFG."""
import json
import os
import random
import re
import sys
import time
from urllib.parse import parse_qs, urlsplit

import httpx
import pyotp

d, port = os.environ["DOMAIN"], int(os.environ["PORT"])
auth = httpx.Client(base_url=f"http://auth-api.{d}", timeout=30)


def totp_login():
    body = {"phone": os.environ["E2E_ADMIN_PHONE"], "password": os.environ["E2E_ADMIN_PASSWORD"], "product": "auth", "app": "admin"}
    return auth.post("/v1/login", json={**body, "totp": pyotp.TOTP(os.environ["E2E_ADMIN_TOTP_SECRET"]).now()})


r = totp_login()
if r.status_code == 401:
    time.sleep(31)
    r = totp_login()
admin_token = r.json()["access_token"]
admin = {"Authorization": "Bearer " + admin_token}


def phone():
    return "07%08d" % random.randint(10000000, 99999999)


def relay(purpose, ph):
    q = auth.get("/v1/admin/relay-queue", headers=admin).json()
    item = next(i for i in q if i["purpose"] == purpose and i["phone_masked"].endswith(ph[-2:]))
    return parse_qs(urlsplit(auth.post(f"/v1/admin/relay/{item['id']}/send", headers=admin).json()["url"]).query)["text"][0]


def make_user(name):
    ph, pw = phone(), "Live-Secret-2026"
    auth.post("/v1/register", json={"phone": ph, "password": pw, "display_name": name, "product": "flariane", "consent": True})
    code = re.search(r"\*(\d{6})\*", relay("activation", ph)).group(1)
    assert auth.post("/v1/activate", json={"phone": ph, "code": code}).status_code == 200
    return ph, pw


a_ph, a_pw = make_user("Live A")
b_ph, _ = make_user("Live B")
auth.post("/v1/password/reset-request", json={"phone": b_ph, "product": "flariane"})
temp = re.search(r"\*([a-z0-9]{10})\*", relay("password_reset", b_ph)).group(1)
print(json.dumps({"port": port, "domain": d, "adminToken": admin_token,
                  "a": {"phone": a_ph, "password": a_pw}, "b": {"phone": b_ph, "temp": temp}, "new": {"phone": phone(), "password": "Live-New-Secret-2026"}}))
