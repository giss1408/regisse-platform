"""LOCAL CHAIN RUN for scolaris on the platform: real auth + the real scolaris backend over HTTP (Caddy). Two parts (see e2e_scolaris.sh):
part 1 creates the platform admin at auth; the shell then runs `create-school` inside scolaris-web (enrols the director at auth);
part 2: the team sends the activation code, the director activates, signs in at auth, and uses scolaris."""
import os
import random
import re
import sys
import time
from datetime import date, timedelta
from urllib.parse import parse_qs, urlsplit

import httpx
import pyotp

PART = sys.argv[1]
D = os.environ["DOMAIN"]
auth = httpx.Client(base_url=f"http://auth-api.{D}", timeout=30)
sco = httpx.Client(base_url=f"http://scolaris-api.{D}", timeout=30)
ok = fail = 0
PW = "Ecole-Pass-2026x"


def check(name, cond, extra=""):
    global ok, fail
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "  -> " + str(extra)[:300]), flush=True)
    ok += bool(cond)
    fail += not cond


def H(tok):
    return {"Authorization": "Bearer " + tok}


def code_of(url):
    return re.search(r"\*(\d{6})\*", parse_qs(urlsplit(url).query)["text"][0]).group(1)


def next_totp(secret, last_step):
    while int(time.time()) // 30 <= last_step:
        time.sleep(1)
    step = int(time.time()) // 30
    return pyotp.TOTP(secret).at(step * 30), step


def login(ph, pw, app, **extra):
    r = auth.post("/v1/login", json={"phone": ph, "password": pw, "product": "scolaris", "app": app, **extra})
    return r.status_code, r.json()


if PART == "1":
    b = {"phone": os.environ["E2E_ADMIN_PHONE"], "password": os.environ["E2E_ADMIN_PASSWORD"], "product": "auth", "app": "admin"}
    code, step = next_totp(os.environ["E2E_ADMIN_TOTP_SECRET"], 0)
    r = auth.post("/v1/login", json={**b, "totp": code})
    if r.status_code == 401:
        time.sleep(31)
        code, step = next_totp(os.environ["E2E_ADMIN_TOTP_SECRET"], step)
        r = auth.post("/v1/login", json={**b, "totp": code})
    boss = H(r.json()["access_token"])
    admin_ph = "07%08d" % random.randint(10000000, 99999999)
    made = auth.post("/v1/admin/staff", headers=boss, json={"phone": admin_ph, "display_name": "Admin Scolaris", "product": "scolaris", "role": "platform_admin"}).json()
    secret, temp = made["totp"]["secret"], made["temporary_password"]
    check("a super admin creates scolaris's platform admin at auth", bool(secret and temp), made)
    code, astep = next_totp(secret, 0)
    s, t = login(admin_ph, temp, "admin", totp=code)
    auth.post("/v1/password/change", headers=H(t["access_token"]), json={"current_password": temp, "new_password": PW})
    open("/state/admin", "w").write(f"{admin_ph} {secret} {astep}")
    print(f"\n{ok} passed, {fail} failed (part 1)")
    sys.exit(1 if fail else 0)

# ---- part 2
admin_ph, secret, astep = open("/state/admin").read().split()
dir_ph = os.environ["DIRECTOR_PHONE"]
code, astep = next_totp(secret, int(astep))
s, t = login(admin_ph, PW, "admin", totp=code)
adm = H(t["access_token"])


def relay_code(purpose, tail):
    rows = [x for x in auth.get("/v1/admin/relay-queue", params={"status": "pending"}, headers=adm).json() if x["purpose"] == purpose and x["phone_masked"].endswith(tail)]
    return code_of(auth.post(f"/v1/admin/relay/{rows[-1]['id']}/send", headers=adm).json()["url"])


s, pre = login(dir_ph, PW, "staff")
check("the director cannot sign in before activation", s != 200, pre)
check("the team sends the WhatsApp code and the director activates with a password of her own",
      auth.post("/v1/activate", json={"phone": dir_ph, "code": relay_code("activation", dir_ph[-2:]), "password": PW}).status_code == 200)
s, tok = login(dir_ph, PW, "staff")
check("she signs in at auth in the staff app", s == 200 and tok["role"] == "director", tok)
d = H(tok["access_token"])
me = sco.get("/v1/me", headers=d)
check("scolaris accepts the auth token (verified locally with the JWKS) and knows her school", me.status_code == 200 and me.json()["staff"]["role"] == "director" and me.json()["school"]["name"] == "Ecole E2E", me.text)
year = me.json()["current_year"]["id"]
check("a token for another product's app is refused", sco.get("/v1/me", headers=H(auth.post("/v1/login", json={"phone": os.environ["E2E_ADMIN_PHONE"], "password": os.environ["E2E_ADMIN_PASSWORD"], "product": "auth", "app": "admin", "totp": "000000"}).json().get("access_token", "x"))).status_code in (401, 403))
room = sco.post("/v1/classrooms", headers=d, json={"year_id": year, "level": "CM2", "name": "CM2 A"}).json()
first = date.today() - timedelta(days=15)
fees = sco.put("/v1/fees", headers=d, json={"year_id": year, "level": "CM2", "lines": [
    {"kind": "inscription", "label": "Inscription", "amount": 25000, "due_date": first.isoformat()},
    {"kind": "scolarite", "label": "1re tranche", "amount": 60000, "due_date": (first + timedelta(days=5)).isoformat()}]})
check("the director sets up a class and the fee schedule", fees.status_code == 200 and room.get("id"), fees.text)
st = sco.post("/v1/students", headers=d, json={"last_name": "Traoré", "first_names": "Fanta", "sex": "F", "enrollment": {"classroom_id": room["id"]},
                                               "contacts": [{"name": "Traoré Moussa", "relation": "pere", "phone": os.environ["PARENT_PHONE"], "financial_responsible": True}]})
check("a student is enrolled with her parent's phone", st.status_code == 201, st.text)
enr = st.json()["enrollment_id"]
check("the family shows in the arrears (late, 85 000 FCFA)", [f["overdue_total"] for f in sco.get(f"/v1/arrears?year_id={year}", headers=d).json()] == [85000])
pay = sco.post(f"/v1/enrollments/{enr}/payments", headers=d, json={"amount": 40000, "method": "wave", "reference": "WV-E2E-1"})
check("a Wave payment gets a receipt number and reduces the arrears", pay.status_code == 201 and pay.json()["payment"]["receipt_number"].startswith("REC-") and pay.json()["account"]["remaining"] == 45000, pay.text)
r = sco.post(f"/v1/payments/{pay.json()['payment']['id']}/cancel", headers=d, json={"reason": "Test annulation"})
check("cancelling a payment needs the password again (just signed in: allowed) and creates a credit note", r.status_code == 201 and r.json()["credit_note"]["receipt_number"].startswith("AV-"), r.text)
acc = sco.post("/v1/staff", headers=d, json={"display_name": "Caissier E2E", "phone": "07%08d" % random.randint(10000000, 99999999), "role": "accountant"})
check("the director enrols an accountant (a pending account at auth)", acc.status_code == 201, acc.text)
# ---- grades and report card (slice 2)
mt = sco.post("/v1/subjects", headers=d, json={"name": "Mathématiques"}).json()["id"]
fr = sco.post("/v1/subjects", headers=d, json={"name": "Français"}).json()["id"]
sco.put("/v1/coefficients", headers=d, json={"year_id": year, "level": "CM2", "lines": [{"subject_id": mt, "coef": 3}, {"subject_id": fr, "coef": 2}]})
ev = sco.post("/v1/evaluations", headers=d, json={"classroom_id": room["id"], "subject_id": mt, "period": 1, "kind": "composition"}).json()["id"]
check("the director enters marks", sco.put(f"/v1/evaluations/{ev}/grades", headers=d, json={"grades": [{"enrollment_id": enr, "value": "15.5"}]}).status_code == 200)
res = sco.get(f"/v1/classrooms/{room['id']}/results?period=1", headers=d).json()
check("results give the average and rank 1", res["rows"][0]["average"] == "15.50" and res["rows"][0]["rank"]["position"] == 1, res)
check("the report card carries the average and rank", sco.get(f"/v1/enrollments/{enr}/report-card?period=1", headers=d).json()["average"] == "15.50")
check("the director locks the evaluation, then marks are frozen", sco.post(f"/v1/evaluations/{ev}/lock", headers=d).status_code == 200 and sco.put(f"/v1/evaluations/{ev}/grades", headers=d, json={"grades": [{"enrollment_id": enr, "value": "3"}]}).status_code == 409)
# ---- reminders (slice 3): 15 days late, no WhatsApp opt-in -> a phone call, one message for the family
rem = sco.get("/v1/reminders", headers=d).json()
check("a late family gets ONE reminder, planned once (a phone call: no opt-in)", [(i["step"], i["channel"]) for i in rem["items"]] == [("late_10", "appel")], rem)
check("running the planner again plans nothing new", sco.get("/v1/reminders", headers=d).json()["planned"] == 0)
p2 = sco.post(f"/v1/enrollments/{enr}/payments", headers=d, json={"amount": 1000, "method": "especes"}).json()["payment"]
auth.post("/v1/logout", json={"refresh_token": tok["refresh_token"]}, headers=d)
r = sco.post(f"/v1/payments/{p2['id']}/cancel", headers=d, json={"reason": "Apres deconnexion"})
check("money cannot be cancelled once her session was ended at auth, although the token has not expired", r.status_code == 403 and r.json()["detail"] == "session_ended", r.text)
# ---- a teacher is linked to HER class and subject (permissions)
tp = "07%08d" % random.randint(10000000, 99999999)
made = sco.post("/v1/staff", headers=d, json={"display_name": "Prof E2E", "phone": tp, "role": "teacher"})
check("the director enrols a teacher", made.status_code == 201, made.text)
check("the team sends the WhatsApp code and the teacher activates", auth.post("/v1/activate", json={"phone": tp, "code": relay_code("activation", tp[-2:]), "password": PW}).status_code == 200)
s3, ttok = login(tp, PW, "staff")
th = H(ttok["access_token"])
check("she signs in at auth in the staff app as a teacher", s3 == 200 and ttok["role"] == "teacher", ttok)
check("with no assignment she sees no class and not the school register", sco.get(f"/v1/classrooms?year_id={year}", headers=th).json() == [] and sco.get(f"/v1/students?year_id={year}", headers=th).status_code == 403)
r = sco.post("/v1/evaluations", headers=th, json={"classroom_id": room["id"], "subject_id": mt, "period": 1, "kind": "devoir"})
check("she cannot create an evaluation in a class she was not given", r.status_code == 403 and r.json()["detail"] == "not_your_class", r.text)
check("only the director assigns teachers", sco.put(f"/v1/classrooms/{room['id']}/teachers", headers=th, json={"assignments": []}).status_code == 403)
a = sco.put(f"/v1/classrooms/{room['id']}/teachers", headers=d, json={"head_teacher_id": made.json()["id"], "assignments": [{"staff_id": made.json()["id"], "subject_id": mt}]})
check("the director assigns her to the class for maths and makes her head teacher", a.status_code == 200, a.text)
mine = sco.get(f"/v1/classrooms?year_id={year}", headers=th).json()
check("now she sees exactly her class, flagged as hers", [(c["name"], c["is_head"]) for c in mine] == [("CM2 A", True)], mine)
check("her subject list is limited to maths", [x["name"] for x in sco.get(f"/v1/subjects?classroom_id={room['id']}", headers=th).json()] == ["Mathématiques"])
check("she creates a maths evaluation but not a French one", sco.post("/v1/evaluations", headers=th, json={"classroom_id": room["id"], "subject_id": mt, "period": 1, "kind": "devoir"}).status_code == 201
      and sco.post("/v1/evaluations", headers=th, json={"classroom_id": room["id"], "subject_id": fr, "period": 1, "kind": "devoir"}).json()["detail"] == "not_your_subject")
check("as head teacher she writes conduct, but not the council's decision", sco.put(f"/v1/enrollments/{enr}/remarks", headers=th, json={"period": 1, "conduct": "16"}).status_code == 200
      and sco.put(f"/v1/enrollments/{enr}/remarks", headers=th, json={"period": 3, "decision": "passage"}).json()["detail"] == "director_only")
sco.put(f"/v1/classrooms/{room['id']}/teachers", headers=d, json={"head_teacher_id": None, "assignments": []})
check("when the director removes her, the class is closed to her again", sco.get(f"/v1/classrooms/{room['id']}/results?period=1", headers=th).json()["detail"] == "not_your_class")
# ---- parent space (slice 4): she signs up herself at auth, the team sends the code, she sees HER child only
pp = os.environ["PARENT_PHONE"]
r = auth.post("/v1/register", json={"phone": pp, "password": PW, "display_name": "Traoré Moussa", "product": "scolaris", "consent": True})
check("a parent signs up at auth (pending account)", r.status_code in (200, 201), r.text)
check("the team sends her WhatsApp code and she activates", auth.post("/v1/activate", json={"phone": pp, "code": relay_code("activation", pp[-2:])}).status_code == 200)
s2, ptok = login(pp, PW, "parent")
check("she signs in at auth in the parent app", s2 == 200 and ptok["role"] == "parent", ptok)
ph = H(ptok["access_token"])
kids = sco.get("/v1/parent/children", headers=ph).json()
check("scolaris shows her child by her verified phone number", [k["name"] for k in kids] == ["Fanta TRAORÉ"] and kids[0]["balance"]["total_due"] == 85000, kids)
check("a parent token is refused by the staff routes", sco.get("/v1/me", headers=ph).status_code == 403 and sco.get(f"/v1/arrears?year_id={year}", headers=ph).status_code == 403)
check("she reads the report card of her child", sco.get(f"/v1/parent/children/{kids[0]['enrollment_id']}/report-card?period=1", headers=ph).json()["average"] == "15.50")
check("the director's exports work", sco.get(f"/v1/exports/headcount?year_id={year}", headers=d).json()["total"]["total"] == 1 and sco.get(f"/v1/exports/headcount?year_id={year}&format=csv", headers=d).text.lstrip("\ufeff").startswith("Niveau;"))
print(f"\n{ok} passed, {fail} failed (part 2)")
sys.exit(1 if fail else 0)
