"""Prepares one scenario for the admin-app live smoke (Dart): prints the JSON config for --dart-define=LIVE_CFG."""
import json
import os
import sys
import time

import httpx
import pyotp

scenario = sys.argv[1]
d, port = os.environ["DOMAIN"], int(os.environ["PORT"])
auth = httpx.Client(base_url=f"http://auth-api.{d}", timeout=30)
email, pw, secret = "e2e@flariane.test", os.environ["FLR_E2E_PASSWORD"], os.environ["FLR_E2E_TOTP"]
dev = json.loads(os.environ["FLR_E2E_DEVICE"])
cfg = {"scenario": scenario, "port": port, "domain": d, "email": email, "password": pw, "device": dev,
       # the code of the NEXT 30 s step: accepted (window +-1) and newer than any code already used by an earlier login
       "totp": pyotp.TOTP(secret).at(time.time() + 30)}
if scenario == "forced":
    body = {"phone": os.environ["E2E_ADMIN_PHONE"], "password": os.environ["E2E_ADMIN_PASSWORD"], "product": "auth", "app": "admin"}
    boss_secret = os.environ["E2E_ADMIN_TOTP_SECRET"]
    r = auth.post("/v1/login", json={**body, "totp": pyotp.TOTP(boss_secret).now()})
    if r.status_code == 401:
        time.sleep(31)
        r = auth.post("/v1/login", json={**body, "totp": pyotp.TOTP(boss_secret).now()})
    boss = {"Authorization": "Bearer " + r.json()["access_token"]}
    uid = auth.get("/v1/admin/users/search", params={"email": email}, headers=boss).json()["id"]
    cfg["temp"] = auth.post(f"/v1/admin/staff/{uid}/reset", headers=boss, json={"reset_totp": False}).json()["temporary_password"]
print(json.dumps(cfg))
