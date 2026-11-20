"""Prints a short-lived access token of the e2e back-office super admin (for smoke tests that play the team sending WhatsApp codes)."""
import os
import time

import httpx
import pyotp

a = httpx.Client(base_url=f"http://auth-api.{os.environ['DOMAIN']}", timeout=20)
body = {"phone": os.environ["E2E_ADMIN_PHONE"], "password": os.environ["E2E_ADMIN_PASSWORD"], "product": "auth", "app": "admin"}
for _ in range(2):
    r = a.post("/v1/login", json={**body, "totp": pyotp.TOTP(os.environ["E2E_ADMIN_TOTP_SECRET"]).now()})
    if r.status_code == 200:
        break
    time.sleep(31)  # the authenticator code of the previous step was just used
print(r.json()["access_token"])
