"""End-to-end run of the auth service through Caddy (the real deployed path), consuming tokens the way a product would
(regisse_kit.sso against the live JWKS). Never run against production: only local/test hosts are accepted.

  E2E_AUTH_URL=http://auth-api.localhost  E2E_ADMIN_PHONE=... E2E_ADMIN_PASSWORD=... E2E_ADMIN_TOTP_SECRET=... E2E_SERVICE_KEY=...
Exit code 0 only if every check passed.
"""

import os
import random
import re
import sys
import time
from urllib.parse import parse_qs, urlsplit

import httpx
import pyotp
from regisse_kit.sso import SsoError, SsoVerifier

API = os.environ.get("E2E_AUTH_URL", "").rstrip("/")
PASSWORD = "E2e-Secret-2026"
results: list[tuple[str, bool]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    results.append((name, bool(ok)))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{'' if ok else '  -> ' + detail}", flush=True)
    return bool(ok)


def section(t: str) -> None:
    print(f"\n== {t}", flush=True)


def wa_text(url: str) -> str:
    return parse_qs(urlsplit(url).query)["text"][0]


def main() -> int:
    host = httpx.URL(API).host if API else ""
    if not API or not (host.endswith(".localhost") or host in ("localhost", "127.0.0.1") or host.endswith(".test")):
        sys.exit("Set E2E_AUTH_URL to a LOCAL/TEST auth host. Refusing to run elsewhere.")
    http = httpx.Client(base_url=API, timeout=30)
    phone = f"07{random.randint(10000000, 99999999)}"

    section("Server")
    check("healthz", http.get("/healthz").status_code == 200)
    check("readyz (db)", http.get("/readyz").json().get("checks", {}).get("db") == "ok")
    jwks_url = f"{API}/.well-known/jwks.json"
    check("JWKS published with an Ed25519 key", any(k.get("crv") == "Ed25519" for k in http.get("/.well-known/jwks.json").json()["keys"]))

    section("Back office login (password + authenticator code)")
    secret = os.environ["E2E_ADMIN_TOTP_SECRET"]
    body = {"phone": os.environ["E2E_ADMIN_PHONE"], "password": os.environ["E2E_ADMIN_PASSWORD"], "product": "auth", "app": "admin"}
    r = http.post("/v1/login", json=body)
    check("admin login refused without authenticator code", r.status_code == 401 and r.json()["detail"] == "invalid_totp", r.text)
    r = http.post("/v1/login", json={**body, "totp": pyotp.TOTP(secret).now()})
    if r.status_code == 401:  # the code of this 30 s step was used by the previous run: wait for the next step
        time.sleep(31)
        r = http.post("/v1/login", json={**body, "totp": pyotp.TOTP(secret).now()})
    if not check("admin login with authenticator code", r.status_code == 200, r.text):
        return 1
    admin = {"Authorization": f"Bearer {r.json()['access_token']}"}
    issuer = http.get("/.well-known/jwks.json")  # warm
    del issuer

    section("A. Customer registers, an admin relays the code, the customer activates")
    r = http.post("/v1/register", json={"phone": phone, "password": PASSWORD, "display_name": "E2E Fatou", "product": "flariane", "consent": True})
    check("register", r.status_code == 201, r.text)
    check("login blocked until activation", http.post("/v1/login", json={"phone": phone, "password": PASSWORD, "product": "flariane", "app": "customer"}).json().get("detail") == "not_activated")
    queue = http.get("/v1/admin/relay-queue", headers=admin).json()
    item = next((i for i in queue if i["purpose"] == "activation" and i["display_name"] == "E2E Fatou" and i["phone_masked"].endswith(phone[-2:])), None)
    check("request is in the relay queue with a masked number", item is not None and phone not in str(queue), str(queue)[:200])
    url = http.post(f"/v1/admin/relay/{item['id']}/send", headers=admin).json()["url"]
    text = wa_text(url)
    code = re.search(r"\*(\d{6})\*", text).group(1)
    check("admin gets a wa.me link to the customer's number with the message written", url.startswith("https://wa.me/225" + phone) and "Flariane" in text)
    check("wrong code refused", http.post("/v1/activate", json={"phone": phone, "code": "000000" if code != "000000" else "111111"}).status_code == 400)
    check("right code activates", http.post("/v1/activate", json={"phone": phone, "code": code}).status_code == 200)

    section("B. Login, and a product verifying the token locally")
    r = http.post("/v1/login", json={"phone": phone, "password": PASSWORD, "product": "flariane", "app": "customer"})
    check("login", r.status_code == 200, r.text)
    t = r.json()
    verifier = SsoVerifier(issuer=os.environ["E2E_ISSUER"], audience="flariane", jwks_fetch=lambda: httpx.get(jwks_url, timeout=10).json())
    p = verifier.verify(t["access_token"])
    check("kit.sso verifies the token with the live JWKS", p.role == "customer" and p.phone_key == f"tel:+225{phone}" and p.amr == ["pwd"], str(p))
    try:
        SsoVerifier(issuer=os.environ["E2E_ISSUER"], audience="fidelia", jwks_fetch=lambda: httpx.get(jwks_url).json()).verify(t["access_token"])
        check("token for flariane is refused by fidelia (audience)", False)
    except SsoError:
        check("token for flariane is refused by fidelia (audience)", True)
    try:
        p.require_step_up(300, amr="totp")
        check("password-only session cannot pass a step-up check", False)
    except SsoError as e:
        check("password-only session cannot pass a step-up check", e.code == "step_up_required")
    me = http.get("/v1/me", headers={"Authorization": f"Bearer {t['access_token']}"}).json()
    check("/v1/me masks the phone", me["phone_masked"].startswith("+225 07 ••") and phone not in str(me))
    check("customer cannot use admin endpoints", http.get("/v1/admin/relay-queue", headers={"Authorization": f"Bearer {t['access_token']}"}).status_code == 403)
    check("customer has no admin app access", http.post("/v1/login", json={"phone": phone, "password": PASSWORD, "product": "flariane", "app": "admin"}).status_code == 403)

    section("C. Sessions")
    r2 = http.post("/v1/refresh", json={"refresh_token": t["refresh_token"]})
    check("refresh rotates", r2.status_code == 200 and r2.json()["refresh_token"] != t["refresh_token"])
    check("reusing the old refresh token is refused", http.post("/v1/refresh", json={"refresh_token": t["refresh_token"]}).status_code == 401)
    check("...and ends the whole session", http.post("/v1/refresh", json={"refresh_token": r2.json()["refresh_token"]}).status_code == 401)
    t = http.post("/v1/login", json={"phone": phone, "password": PASSWORD, "product": "flariane", "app": "customer"}).json()
    key = os.environ["E2E_SERVICE_KEY"]
    intro = lambda: http.post("/v1/introspect", json={"token": t["access_token"]}, headers={"X-Service-Key": key}).json()  # noqa: E731
    check("introspect says active", intro().get("active") is True)
    check("logout", http.post("/v1/logout", json={"refresh_token": t["refresh_token"]}).status_code == 204)
    check("introspect says inactive after logout (signature still valid, session is not)", intro() == {"active": False})

    section("D. Forgotten password (relay, temporary password, forced change)")
    r = http.post("/v1/password/reset-request", json={"phone": phone, "product": "flariane"})
    r_unknown = http.post("/v1/password/reset-request", json={"phone": "0799999999", "product": "flariane"})
    check("same answer for unknown number", r.status_code == r_unknown.status_code == 202 and r.json() == r_unknown.json())
    item = next(i for i in http.get("/v1/admin/relay-queue", headers=admin).json() if i["purpose"] == "password_reset" and i["phone_masked"].endswith(phone[-2:]))
    temp = re.search(r"\*([a-z0-9]{10})\*", wa_text(http.post(f"/v1/admin/relay/{item['id']}/send", headers=admin).json()["url"])).group(1)
    check("old password stops working", http.post("/v1/login", json={"phone": phone, "password": PASSWORD, "product": "flariane", "app": "customer"}).status_code == 401)
    r = http.post("/v1/login", json={"phone": phone, "password": temp, "product": "flariane", "app": "customer"})
    check("temporary password works and forces a change", r.status_code == 200 and r.json()["must_change_password"] is True)
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}
    check("customer sets a new password", http.post("/v1/password/change", headers=h, json={"current_password": temp, "new_password": "Brand-New-Pass-1"}).status_code == 204)
    check("login with the new password", http.post("/v1/login", json={"phone": phone, "password": "Brand-New-Pass-1", "product": "flariane", "app": "customer"}).json()["must_change_password"] is False)

    section("E. Admin controls")
    uid = http.get("/v1/admin/users/search", params={"phone": phone}, headers=admin).json()["id"]
    check("admin disables the user", http.post(f"/v1/admin/users/{uid}/disable", headers=admin).status_code == 204)
    check("disabled user cannot log in", http.post("/v1/login", json={"phone": phone, "password": "Brand-New-Pass-1", "product": "flariane", "app": "customer"}).json().get("detail") == "account_blocked")
    check("admin re-enables", http.post(f"/v1/admin/users/{uid}/enable", headers=admin).status_code == 204)
    section("F. Staff accounts (super admin creates them in person)")
    staff_phone = f"07{random.randint(10000000, 99999999)}"
    r = http.post("/v1/admin/staff", headers=admin, json={"phone": staff_phone, "display_name": "E2E Agent", "product": "fidelia", "role": "agent"})
    check("super admin creates an agent (no authenticator for agents)", r.status_code == 201 and r.json()["totp"] is None and r.json()["temporary_password"], r.text)
    staff = r.json()
    r = http.post("/v1/login", json={"phone": staff_phone, "password": staff["temporary_password"], "product": "fidelia", "app": "agent"})
    check("agent logs in with the temporary password and must change it", r.status_code == 200 and r.json()["must_change_password"] is True, r.text)
    check("agent cannot call admin endpoints", http.get("/v1/admin/relay-queue", headers={"Authorization": f"Bearer {r.json()['access_token']}"}).status_code == 403)
    check("admin deactivates the agent", http.post(f"/v1/admin/users/{staff['user_id']}/disable", headers=admin).status_code == 204)
    check("deactivated agent is refused", http.post("/v1/login", json={"phone": staff_phone, "password": staff["temporary_password"], "product": "fidelia", "app": "agent"}).json().get("detail") == "account_blocked")
    r = http.post("/v1/admin/staff", headers=admin, json={"phone": f"07{random.randint(10000000, 99999999)}", "display_name": "E2E Op", "product": "flariane", "role": "operator"})
    check("staff creation returns an authenticator secret for operators", r.status_code == 201 and r.json()["totp"] and r.json()["totp"]["uri"].startswith("otpauth://"), r.text)

    # (admin-vs-super-admin permissions are covered by the unit tests: this e2e admin is a super admin)

    total, ok = len(results), sum(1 for _, k in results if k)
    print(f"\n{ok}/{total} checks passed")
    for n, k in results:
        if not k:
            print(f"  FAILED: {n}")
    return 0 if ok == total else 1


if __name__ == "__main__":
    sys.exit(main())
