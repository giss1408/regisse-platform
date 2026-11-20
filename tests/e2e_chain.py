"""LOCAL FULL-CHAIN RUN (not a unit test): the real auth service, the real fidelia backend (Postgres, migrated by alembic, SSO on, hooks
wired both ways) and the real data import, driven over HTTP the way the apps drive them. Phases: p1 (everything but the outage), p2 (a
deletion while auth is DOWN), p3 (after auth is back and the outbox ran). State passes between phases in /state/state.json."""
import json
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
fid = httpx.Client(base_url="http://fidelia-chain:8000", timeout=30)
ok = fail = 0
STATE = "/state/state.json"
CONSENT = "fidelite-2026-10"
PW = "Chain-Pass-2026x"


def check(name, cond, extra=""):
    global ok, fail
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "  -> " + str(extra)[:300]), flush=True)
    ok += bool(cond)
    fail += not cond


def H(tok):
    return {"Authorization": "Bearer " + tok}


def code_of(url):
    return re.search(r"\*(\d{6})\*", parse_qs(urlsplit(url).query)["text"][0]).group(1)


def temp_of(url):
    return re.search(r"\*([a-z0-9]+)\*", parse_qs(urlsplit(url).query)["text"][0]).group(1)


def rnd():
    return "07%08d" % random.randint(10000000, 99999999)


def login(ph, pw, app, **extra):
    r = auth.post("/v1/login", json={"phone": ph, "password": pw, "product": "fidelia", "app": app, **extra})
    return r.status_code, r.json()


def next_totp(secret, last_step):
    """A code from a step after `last_step` (auth refuses the replay of a step)."""
    while int(time.time()) // 30 <= last_step:
        time.sleep(1)
    step = int(time.time()) // 30
    return pyotp.TOTP(secret).at(step * 30), step


def queue(h, purpose=None, status="pending"):
    rows = auth.get("/v1/admin/relay-queue", params={"status": status}, headers=h).json()
    return [r for r in rows if purpose is None or r["purpose"] == purpose]


def signup(h_admin, name):
    ph = rnd()
    auth.post("/v1/register", json={"phone": ph, "password": PW, "display_name": name, "product": "fidelia", "consent": True})
    item = [i for i in queue(h_admin, "activation") if i["phone_masked"].endswith(ph[-2:])][-1]
    code = code_of(auth.post(f"/v1/admin/relay/{item['id']}/send", headers=h_admin).json()["url"])
    return ph, auth.post("/v1/activate", json={"phone": ph, "code": code}).status_code


def p1():
    st = {}
    # ---- 0. the import ran before this script (shell); the fidelia admin gets her authenticator from a super admin
    boss_body = {"phone": os.environ["E2E_ADMIN_PHONE"], "password": os.environ["E2E_ADMIN_PASSWORD"], "product": "auth", "app": "admin"}
    code, step = next_totp(os.environ["E2E_ADMIN_TOTP_SECRET"], 0)
    r = auth.post("/v1/login", json={**boss_body, "totp": code})
    if r.status_code == 401:
        time.sleep(31)
        code, step = next_totp(os.environ["E2E_ADMIN_TOTP_SECRET"], step)
        r = auth.post("/v1/login", json={**boss_body, "totp": code})
    boss = H(r.json()["access_token"])
    admin_id = auth.get("/v1/admin/users/search", params={"phone": "0700000009"}, headers=boss).json()
    check("the imported fidelia admin exists in auth with the admin role (no authenticator yet)", any(m == {"product": "fidelia", "role": "admin"} for m in admin_id["memberships"]), admin_id)
    s, body = login("0700000009", PW, "admin")
    check("...and cannot sign in before a super admin gives her a code", s in (401, 403), body)
    reset = auth.post(f"/v1/admin/staff/{admin_id['id']}/reset", headers=boss, json={"reset_totp": True}).json()
    secret, temp = reset["totp"]["secret"], reset["temporary_password"]
    code, step = next_totp(secret, 0)
    s, t = login("0700000009", temp, "admin", totp=code)
    check("the fidelia admin signs in with her temporary password and authenticator code (forced password change)", s == 200 and t["must_change_password"], t)
    ch = auth.post("/v1/password/change", headers=H(t["access_token"]), json={"current_password": temp, "new_password": PW})
    t = auth.post("/v1/refresh", json={"refresh_token": t["refresh_token"]}).json()
    adm = H(t["access_token"])
    check("password changed, session renewed without the mark", ch.status_code == 204 and not t["must_change_password"], t)
    me = fid.get("/api/auth/me", headers=adm)
    check("fidelia accepts the admin's auth token", me.status_code == 200 and me.json()["role"] == "admin", me.text)

    # ---- 1. the team's relay, from the fidelia admin (scoped to fidelia), first the seed merchant who has no password yet
    auth.post("/v1/password/reset-request", json={"phone": "0700000002", "product": "fidelia"})
    pend = queue(adm, "password_reset")
    check("the fidelia admin sees the merchant's password-reset request (and only fidelia's queue)", len(pend) == 1 and all(x["product"] == "fidelia" for x in queue(adm)), pend)
    url = auth.post(f"/v1/admin/relay/{pend[0]['id']}/send", headers=adm).json()["url"]
    mtemp = temp_of(url)
    s, mt = login("0700000002", mtemp, "merchant")
    check("the merchant signs in with the temporary password the team sent", s == 200 and mt["must_change_password"], mt)
    auth.post("/v1/password/change", headers=H(mt["access_token"]), json={"current_password": mtemp, "new_password": PW})
    mt = auth.post("/v1/refresh", json={"refresh_token": mt["refresh_token"]}).json()
    pay = fid.get("/api/merchant/pay-code", headers=H(mt["access_token"]))
    check("fidelia finds the merchant's shop from her auth token (the imported number, the seeded shop)", pay.status_code == 200 and pay.json().get("pay_code"), pay.text)

    # ---- 2. a customer signs up through auth, the team activates, fidelia sees her
    c1, st1 = signup(adm, "Cliente Un")
    check("sign-up -> relay activation -> activate", st1 == 200)
    s, t1 = login(c1, PW, "customer")
    ch1 = H(t1["access_token"])
    r = fid.put("/api/customer/loyalty-consent", headers=ch1, json={"consent_version": CONSENT})
    check("fidelia opens her account from the token and records her loyalty consent", r.status_code == 200 and r.json()["active"], r.text)

    # ---- 3. number change: request -> alert + code through the relay -> code typed -> scheduled (24 h)
    new1 = rnd()
    r = auth.post("/v1/me/phone/request", headers=ch1, json={"new_phone": new1, "current_password": PW})
    check("number change requested (password required)", r.status_code == 202, r.text)
    rows = auth.get("/v1/admin/phone-changes", headers=adm).json()
    row = [x for x in rows if x["user_id"] and x["new_phone_masked"].endswith(new1[-2:])][-1]
    a_url = auth.post(f"/v1/admin/relay/{row['relays']['alert']['id']}/send", headers=adm).json()["url"]
    c_url = auth.post(f"/v1/admin/relay/{row['relays']['code']['id']}/send", headers=adm).json()["url"]
    check("the warning goes to the OLD number, the code to the NEW one", f"225{c1}" in a_url and f"225{new1}" in c_url and f"225{new1}" not in a_url)
    cf = auth.post("/v1/me/phone/confirm", headers=ch1, json={"code": code_of(c_url)})
    check("the code only SCHEDULES the change (security delay)", cf.status_code == 200 and cf.json()["status"] == "scheduled", cf.text)
    check("meanwhile the old number works in fidelia, the new does not sign in", fid.get("/api/customer/loyalty-consent", headers=ch1).json()["active"] and login(new1, PW, "customer")[0] == 401)

    # ---- 4. the admin steps up (password + a NEW authenticator code), calls her, makes it effective
    code, step = next_totp(secret, step)
    su = auth.post("/v1/step-up", headers=adm, json={"password": PW, "totp": code})
    check("step-up with a fresh authenticator code", su.status_code == 200 and "totp" in su.json()["amr"], su.text)
    adm = H(su.json()["access_token"])
    short = auth.post(f"/v1/admin/phone-changes/{row['id']}/activate-now", headers=adm, json={"note": "ok"})
    now = auth.post(f"/v1/admin/phone-changes/{row['id']}/activate-now", headers=adm, json={"note": "Appelée sur l'ancien numéro, confirme (chaîne locale)"})
    check("activate-now needs a written note, then takes effect", short.status_code == 422 and now.status_code == 200, now.text)
    check("fidelia's hook moved her data first: the old token is dead, the new number signs in", auth.post("/v1/refresh", json={"refresh_token": t1["refresh_token"]}).status_code == 401 and login(c1, PW, "customer")[0] == 401)
    s, t1b = login(new1, PW, "customer")
    ch1 = H(t1b["access_token"])
    r = fid.get("/api/customer/loyalty-consent", headers=ch1)
    check("FIDELIA FOLLOWED: her consent is under the NEW number (data moved by the hook)", s == 200 and r.status_code == 200 and r.json()["active"], r.text)
    check("a notice for the old number is waiting for the team", any(x["purpose"] == "phone_changed_notice" for x in queue(adm)))

    # ---- 5. 'this wasn't me' and disable/enable
    c2, _ = signup(adm, "Cliente Deux")
    s, t2 = login(c2, PW, "customer")
    ch2 = H(t2["access_token"])
    auth.post("/v1/me/phone/request", headers=ch2, json={"new_phone": rnd(), "current_password": PW})
    row2 = [x for x in auth.get("/v1/admin/phone-changes", headers=adm).json() if x["status"] == "requested"][-1]
    nm = auth.post(f"/v1/admin/phone-changes/{row2['id']}/not-me", headers=adm)
    check("'this wasn't me' cancels the request and freezes the account", nm.status_code == 200 and nm.json() == {"result": "cancelled", "frozen": True}, nm.text)
    check("frozen: she cannot sign in and her live token stops working at auth", login(c2, PW, "customer")[1].get("detail") == "account_blocked" and auth.get("/v1/me", headers=ch2).status_code == 401)
    allfz = auth.get("/v1/admin/frozen-users", headers=adm).json()
    fz = [x for x in allfz if x["display_name"] == "Cliente Deux"]
    check("the admin sees it in the frozen list (own product only)", len(fz) == 1 and fz[0]["reason"] == "not_me" and all(x["products"] == ["fidelia"] for x in allfz), allfz)
    uid2 = fz[0]["id"]
    check("giving it back needs a note", auth.post(f"/v1/admin/users/{uid2}/enable", headers=adm).json().get("detail") == "note_required")
    check("...then the account works again", auth.post(f"/v1/admin/users/{uid2}/enable", headers=adm, json={"note": "Appelée, voix reconnue, tout est en ordre"}).status_code == 204 and login(c2, PW, "customer")[0] == 200)
    hit = auth.get("/v1/admin/users/search", params={"phone": c2}, headers=adm).json()
    check("the admin switches an account off with a reason", auth.post(f"/v1/admin/users/{hit['id']}/disable", headers=adm, json={"note": "Téléphone volé signalé par la cliente"}).status_code == 204 and login(c2, PW, "customer")[1].get("detail") == "account_blocked")
    auth.post(f"/v1/admin/users/{hit['id']}/enable", headers=adm, json={"note": "Retrouvé, appelée et confirmée de vive voix"})

    # ---- 6. deletion with auth UP: fidelia erases, then tells auth
    c3, _ = signup(adm, "Cliente Trois")
    s, t3 = login(c3, PW, "customer")
    ch3 = H(t3["access_token"])
    fid.put("/api/customer/loyalty-consent", headers=ch3, json={"consent_version": CONSENT})
    d = fid.delete("/api/account", headers=ch3)
    check("a customer deletes her account in fidelia", d.status_code == 200, d.text)
    s, body = login(c3, PW, "customer")
    check("...and her identity is erased in auth too (no sign-in, no refresh)", s in (401, 403) and auth.post("/v1/refresh", json={"refresh_token": t3["refresh_token"]}).status_code == 401, body)
    # prepare the outage phase: a fourth customer with a live token
    c4, _ = signup(adm, "Cliente Quatre")
    s, t4 = login(c4, PW, "customer")
    fid.put("/api/customer/loyalty-consent", headers=H(t4["access_token"]), json={"consent_version": CONSENT})
    st.update({"c4": c4, "t4": t4["access_token"]})
    json.dump(st, open(STATE, "w"))
    print(f"\n{ok} passed, {fail} failed (phase 1)")
    sys.exit(1 if fail else 0)


def p2():
    st = json.load(open(STATE))
    d = fid.delete("/api/account", headers=H(st["t4"]))
    check("with auth DOWN the customer can still delete her fidelia account (token verified locally)", d.status_code == 200, d.text)
    print(f"\n{ok} passed, {fail} failed (phase 2)")
    sys.exit(1 if fail else 0)


def p3():
    st = json.load(open(STATE))
    s, body = login(st["c4"], PW, "customer")
    check("after auth is back and the outbox ran, her identity is erased in auth", s in (401, 403), body)
    print(f"\n{ok} passed, {fail} failed (phase 3)")
    sys.exit(1 if fail else 0)


{"p1": p1, "p2": p2, "p3": p3}[sys.argv[1]]()
