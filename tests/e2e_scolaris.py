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

import struct
import zlib

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
# ---- timetable
tid = made.json()["id"]
base = f"/v1/classrooms/{room['id']}/timetable/slots"
p1 = sco.post(base, headers=d, json={"weekday": 1, "start": "08:00", "end": "09:00", "subject_id": mt, "teacher_id": tid, "room": "A1"})
check("the director plans a maths period with the assigned teacher", p1.status_code == 201 and p1.json()["teacher"] == "Prof E2E", p1.text)
clash = sco.post(base, headers=d, json={"weekday": 1, "start": "08:30", "end": "09:30", "subject_id": fr})
check("an overlapping period is refused with a sentence naming the clash", clash.status_code == 409 and clash.json()["detail"] == "class_overlap" and "08:00" in clash.json()["message"], clash.text)
check("a teacher cannot be put on a subject she was not assigned", sco.post(base, headers=d, json={"weekday": 2, "start": "08:00", "end": "09:00", "subject_id": fr, "teacher_id": tid}).json()["detail"] == "teacher_not_assigned")
tt = sco.get(f"/v1/classrooms/{room['id']}/timetable", headers=th).json()
check("the teacher reads the class timetable but cannot edit it", tt["editable"] is False and len(tt["slots"]) == 1 and sco.post(base, headers=th, json={"weekday": 3, "start": "08:00", "end": "09:00", "subject_id": mt}).status_code == 403, tt)
check("her own week lists the period", [(x["weekday"], x["start"], x["classroom"]) for x in sco.get("/v1/timetable/me", headers=th).json()["slots"]] == [(1, "08:00", "CM2 A")])
# ---- the teacher cancels one session of her course, with a reason
d0 = date.today() + timedelta(days=1)
mon = d0 + timedelta(days=(7 - d0.weekday()) % 7)            # the next Monday, strictly after today
mon2 = mon + timedelta(days=7)
sid1 = p1.json()["id"]
cx = f"/v1/timetable/slots/{sid1}/cancellations"
c1 = sco.post(cx, headers=th, json={"on_date": mon.isoformat(), "reason": "maladie", "note": "Grippe"})
check("the teacher cancels her Monday maths session with a reason", c1.status_code == 201 and c1.json()["reason"] == "maladie", c1.text)
check("a date that is not that weekday is refused with a sentence", sco.post(cx, headers=th, json={"on_date": (mon + timedelta(days=1)).isoformat(), "reason": "maladie"}).json()["detail"] == "date_not_that_weekday")
check("the same session cannot be cancelled twice", sco.post(cx, headers=th, json={"on_date": mon.isoformat(), "reason": "formation"}).json()["detail"] == "session_already_cancelled")
listed = sco.get("/v1/timetable/cancellations", headers=d).json()
check("the director sees it with the real reason and a message for the families that does not carry it",
      [(a["date"], a["reason"], a["teacher"]) for a in listed] == [(mon.isoformat(), "maladie", "Prof E2E")] and "enseignant absent" in listed[0]["message"] and "Grippe" not in listed[0]["message"] and "maladie" not in listed[0]["message"], listed)
check("the teacher's own week shows her reason", [x.get("reason") for x in sco.get("/v1/timetable/me", headers=th).json()["cancellations"]] == ["maladie"])
c2 = sco.post(cx, headers=th, json={"on_date": mon2.isoformat(), "reason": "formation"})
check("she restores a session she cancelled", c2.status_code == 201 and sco.delete(f"{cx}/{mon2.isoformat()}", headers=th).status_code == 200 and len(sco.get("/v1/timetable/cancellations", headers=d).json()) == 1)
# ---- the school lets Scolaris message families; the teacher calls the roll DURING her course
check("the director switches automatic WhatsApp on", sco.put("/v1/school/settings", headers=d, json={"auto_whatsapp": True}).json()["auto_whatsapp"] is True)
today = date.today()
roll_ok = today.isoweekday() != 7
if roll_ok:
    # a period that started at 00:00 today (UTC, like Abidjan): the roll can be called whatever time the test runs
    rs = sco.post(base, headers=d, json={"weekday": today.isoweekday(), "start": "00:00", "end": "07:59", "subject_id": mt, "teacher_id": tid})
    check("the director plans a course that has started today", rs.status_code == 201, rs.text)
    rid = rs.json()["id"]
    pupils = sco.get(f"/v1/timetable/slots/{rid}/roll", headers=th, params={"on_date": today.isoformat()}).json()["rows"]
    check("the teacher sees the pupils of the class, all present", [p["status"] for p in pupils] == ["present"], pupils)
    saved = sco.put(f"/v1/timetable/slots/{rid}/roll", headers=th, json={"on_date": today.isoformat(), "entries": [{"enrollment_id": enr, "kind": "absence"}]})
    check("she notes a pupil absent during her course", saved.status_code == 200 and saved.json()["recorded"] == 1, saved.text)
    check("another day cannot be backdated by a teacher", sco.put(f"/v1/timetable/slots/{rid}/roll", headers=th, json={"on_date": (today - timedelta(days=7)).isoformat(), "entries": []}).json()["detail"] == "only_today")
    logd = sco.get("/v1/messages", headers=d).json()
    check("the family had no WhatsApp opt-in: the message is listed as 'to phone', nothing was sent",
          [(m["kind"], m["status"], m["needs_call"]) for m in logd["items"]] == [("absence", "skipped", True)] and "au cours de Mathématiques" in logd["items"][0]["text"], logd)

# ---- the school's OWN WhatsApp number: the app composes, the secretary forwards, replies come to the school
if roll_ok:
    check("the director enters the school's own WhatsApp number and switches the automatic sending off",
          sco.put("/v1/school/settings", headers=d, json={"whatsapp_phone": "07 05 06 07 08", "auto_whatsapp": False}).json() == {"auto_whatsapp": False, "whatsapp_phone": "+2250705060708"})
    parent2 = "01%08d" % random.randint(10000000, 99999999)
    p2 = sco.post("/v1/students", headers=d, json={"last_name": "Soro", "first_names": "Awa", "sex": "F", "enrollment": {"classroom_id": room["id"]},
                                                   "contacts": [{"name": "Soro Ali", "relation": "pere", "phone": parent2, "whatsapp_optin": True, "financial_responsible": True}]})
    check("a pupil whose parent agreed to WhatsApp is enrolled", p2.status_code == 201, p2.text)
    enr2 = p2.json()["enrollment_id"]
    r2 = sco.put(f"/v1/timetable/slots/{rid}/roll", headers=th, json={"on_date": today.isoformat(), "entries": [{"enrollment_id": enr, "kind": "absence"}, {"enrollment_id": enr2, "kind": "absence"}]})
    check("the teacher notes her absent during the course: ONE new parent is to be told (the first was told before)", r2.status_code == 200 and r2.json()["notified"] == 1, r2.text)
    ts = sco.get("/v1/messages/to-send", headers=d).json()
    check("the message waits for the secretary, with the school's number to send from", ts["school_phone"] == "+2250705060708" and len(ts["items"]) == 1 and ts["items"][0]["family"] == "SORO", ts)
    link = ts["items"][0]["wa_link"]
    check("one tap opens WhatsApp on the PARENT's number with the text ready", link.startswith("https://wa.me/225" + parent2[-10:]) and "text=" in link and "Awa" in ts["items"][0]["text"], link)
    check("the count of waiting messages is in the profile", sco.get("/v1/me", headers=d).json()["school"]["messages_to_send"] == 1)
    mid = ts["items"][0]["id"]
    check("forwarded once, a second tap changes nothing", sco.post(f"/v1/messages/{mid}/sent", headers=d).json()["changed"] is True and sco.post(f"/v1/messages/{mid}/sent", headers=d).json()["changed"] is False)
    check("a teacher has no access to the messages", sco.get("/v1/messages/to-send", headers=th).status_code == 403)
    cid = sco.get(f"/v1/students/{p2.json()['id']}", headers=d).json()["contacts"][0]["id"]
    check("a parent who says stop: her consent is withdrawn", sco.put(f"/v1/contacts/{cid}/optin", headers=d, json={"whatsapp_optin": False}).json()["whatsapp_optin"] is False)

# ---- a replacement teacher for the cancelled session
tp2 = "07%08d" % random.randint(10000000, 99999999)
t2 = sco.post("/v1/staff", headers=d, json={"display_name": "Remplaçant E2E", "phone": tp2, "role": "teacher"})
check("the director enrols a second teacher", t2.status_code == 201, t2.text)
check("she activates by WhatsApp code", auth.post("/v1/activate", json={"phone": tp2, "code": relay_code("activation", tp2[-2:]), "password": PW}).status_code == 200)
s4, t2tok = login(tp2, PW, "staff")
t2h = H(t2tok["access_token"])
sub_url = f"/v1/timetable/slots/{sid1}/cancellations/{mon.isoformat()}/substitute"
check("the teacher herself cannot name a replacement", sco.put(sub_url, headers=th, json={"staff_id": t2.json()["id"]}).status_code == 403)
check("the absent teacher cannot be her own replacement", sco.put(sub_url, headers=d, json={"staff_id": tid}).json()["detail"] == "substitute_is_the_teacher")
named = sco.put(sub_url, headers=d, json={"staff_id": t2.json()["id"]})
check("the director names the replacement of the cancelled session", named.status_code == 200 and named.json()["substitute"] == "Remplaçant E2E", named.text)
mine2 = sco.get("/v1/timetable/me", headers=t2h).json()["substitutions"]
check("the replacement sees the session she covers, with its date", [(x["date"], x["classroom"], x["subject"]) for x in mine2] == [(mon.isoformat(), "CM2 A", "Mathématiques")], mine2)
check("everyone reading the class timetable sees who replaces", sco.get(f"/v1/classrooms/{room['id']}/timetable", headers=d).json()["cancellations"][0]["substitute"] == "Remplaçant E2E")

# ---- the school's logo
def png_bytes(w=64, h=64):
    chunk = lambda t, b: struct.pack(">I", len(b)) + t + b + struct.pack(">I", zlib.crc32(t + b))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00")) + chunk(b"IEND", b"")

logo = png_bytes()
check("a script renamed as an image is refused", sco.put("/v1/school/logo", headers={**d, "Content-Type": "image/png"}, content=b"<svg onload=alert(1)>").json()["detail"] == "image_format")
check("only the director sets the logo", sco.put("/v1/school/logo", headers={**th, "Content-Type": "image/png"}, content=logo).status_code == 403)
up = sco.put("/v1/school/logo", headers={**d, "Content-Type": "image/png"}, content=logo)
check("the director uploads the school's logo", up.status_code == 200 and up.json()["has_logo"] is True, up.text)
got = sco.get("/v1/school/logo", headers=t2h)
check("the school's staff download the same image, nosniff, with a validator", got.content == logo and got.headers["content-type"] == "image/png" and got.headers["x-content-type-options"] == "nosniff" and "etag" in got.headers)
check("the app asks again and gets 'not modified'", sco.get("/v1/school/logo", headers={**d, "If-None-Match": got.headers["etag"]}).status_code == 304)

# ---- the Scolaris team: subscription, reminders, guards before a block (the platform admin created at the start)
sid_school = me.json()["school"]["id"]
P = "/v1/platform/schools/%d" % sid_school
check("a school's director is not the Scolaris team", sco.get("/v1/platform/schools", headers=d).json()["detail"] == "not_platform_admin")
check("the platform admin sees the school", any(x["id"] == sid_school for x in sco.get("/v1/platform/schools", headers=adm).json()))
started = (date.today() - timedelta(days=100)).isoformat()
st = sco.put(P + "/subscription", headers=adm, json={"monthly_amount": 50000, "billing_day": 5, "started_on": started, "grace_days": 5})
check("the team sets the monthly subscription: the school is late", st.status_code == 200 and st.json()["late"] is True and st.json()["overdue_months"] >= 2, st.text)
check("she cannot block a school that was never warned", sco.post(P + "/block", headers=adm, json={"reason": "Impayé"}).json()["detail"] == "reminders_required")
rem = sco.post(P + "/notices", headers=adm, json={"kind": "reminder"})
check("she sends a reminder in the app", rem.status_code == 201 and "mensualité" in rem.json()["text"], rem.text)
check("a final notice needs a block date a week ahead", sco.post(P + "/notices", headers=adm, json={"kind": "final_notice", "block_on": (today + timedelta(days=2)).isoformat()}).json()["detail"] == "block_date_too_soon")
fin = sco.post(P + "/notices", headers=adm, json={"kind": "final_notice", "block_on": (today + timedelta(days=10)).isoformat()})
check("she sends the final notice with the announced date", fin.status_code == 201, fin.text)
check("the block is refused until that date has come", sco.post(P + "/block", headers=adm, json={"reason": "Impayé"}).json()["detail"] == "block_date_not_reached")
bill = sco.get("/v1/billing", headers=d).json()
check("the director sees the notices and what is owed in the app", [n["kind"] for n in bill["notices"]] == ["final_notice", "reminder"] and bill["standing"]["late"] is True, bill)
check("a teacher sees no billing", sco.get("/v1/billing", headers=th).status_code == 403)
banner = sco.get("/v1/me", headers=d).json()["billing_banner"]
check("the banner at the top of the app says it is the final notice, with the date", banner["level"] == "final" and banner["block_on"] == (today + timedelta(days=10)).isoformat(), banner)
nid = bill["notices"][0]["id"]
sco.post(f"/v1/billing/notices/{nid}/seen", headers=d)
detail = sco.get(P, headers=adm).json()
check("the team sees the direction read the notice", detail["notices"][0]["seen_by"] == 1 and detail["notices"][0]["audience"] >= 1, detail["notices"][0])
pay1 = sco.post(P + "/payments", headers=adm, json={"amount": 50000, "method": "wave", "reference": "WV-SUB-1"})
check("a payment is recorded and the oldest instalment is paid first", pay1.status_code == 201 and pay1.json()["standing"]["remaining"] == st.json()["remaining"] - 50000, pay1.text)
check("the platform audit keeps what the team did", {"subscription.set", "notice.reminder", "notice.final_notice", "subscription.payment"} <= {a["action"] for a in sco.get(f"/v1/platform/audit?school_id={sid_school}", headers=adm).json()})

# ---- where schools pay their subscription (set by the Scolaris team)
check("a school cannot change where payments go", sco.put("/v1/platform/payment-info", headers=d, json={"methods": [], "default_monthly_amount": 1}).json()["detail"] == "not_platform_admin")
pi = sco.put("/v1/platform/payment-info", headers=adm, json={"methods": [{"operator": "wave", "number": "07 11 22 33 44", "holder": "Scolaris SARL"}, {"operator": "orange_money", "number": "05 11 22 33 44", "holder": "Scolaris SARL"}], "default_monthly_amount": 30000, "note": "Indiquez la référence"})
check("the team enters the Mobile Money numbers and the usual price (password re-proved)", pi.status_code == 200 and pi.json()["default_monthly_amount"] == 30000 and pi.json()["methods"][0]["number"] == "+2250711223344", pi.text)
pt = sco.get("/v1/billing", headers=d).json()["pay_to"]
check("the director sees where to pay, the reference and the amount owed", [m["operator"] for m in pt["methods"]] == ["wave", "orange_money"] and pt["reference"] == f"SCO-{sid_school}" and pt["amount"] > 0 and pt["note"] == "Indiquez la référence", pt)
check("the audit keeps the change with masked numbers", any(a["action"] == "payment_info.set" and "0711223344" not in str(a) for a in sco.get("/v1/platform/audit", headers=adm).json()))

# ---- the WhatsApp webhook is public but verified
check("the webhook refuses an unsigned post and a wrong handshake", sco.post("/webhooks/whatsapp", content=b"{}").status_code == 403 and sco.get("/webhooks/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "x", "hub.challenge": "1"}).status_code == 403)
sco.put(f"/v1/classrooms/{room['id']}/teachers", headers=d, json={"head_teacher_id": None, "assignments": []})
check("when the director removes her, the class is closed to her again", sco.get(f"/v1/classrooms/{room['id']}/results?period=1", headers=th).json()["detail"] == "not_your_class")
after = sco.get(f"/v1/classrooms/{room['id']}/timetable", headers=d).json()["slots"]
check("her periods stay in the timetable, now without a teacher", [(x["subject"], x["teacher"]) for x in after] == [("Mathématiques", None)] * (2 if roll_ok else 1), after)
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
ptt = sco.get(f"/v1/parent/children/{kids[0]['enrollment_id']}/timetable", headers=ph).json()
check("she reads the timetable of her child's class", [x["subject"] for x in ptt["slots"]] == ["Mathématiques"] * (2 if roll_ok else 1))
check("she sees that the session is cancelled and who replaces, never why", ptt["cancellations"] == [{"slot_id": sid1, "date": mon.isoformat(), "substitute": "Remplaçant E2E", "substitute_id": t2.json()["id"]}] and "Grippe" not in str(ptt) and "maladie" not in str(ptt), ptt)
if roll_ok:
    pr = sco.post(f"/v1/parent/children/{kids[0]['enrollment_id']}/absence-reports", headers=ph, json={"day": today.isoformat(), "reason": "maladie", "note": "Palu"})
    check("the parent reports an absence for the day", pr.status_code == 201, pr.text)
    check("the director sees what the parent told the school", [(r["name"], r["reason"]) for r in sco.get("/v1/absence-reports", headers=d).json()] == [("TRAORÉ Fanta", "maladie")])
    ab = sco.get(f"/v1/parent/children/{kids[0]['enrollment_id']}/absences", headers=ph).json()
    check("she sees the course during which her child was noted absent", [(a["slot"], (a["course"] or {}).get("subject")) for a in ab] == [("cours", "Mathématiques")], ab)
check("she gets the logo of her child's school and only that", sco.get(f"/v1/parent/schools/{sid_school}/logo", headers=ph).content == logo and sco.get(f"/v1/parent/schools/{sid_school + 9999}/logo", headers=ph).status_code == 404)
check("a parent token is refused by the staff routes", sco.get("/v1/me", headers=ph).status_code == 403 and sco.get(f"/v1/arrears?year_id={year}", headers=ph).status_code == 403)
check("she reads the report card of her child", sco.get(f"/v1/parent/children/{kids[0]['enrollment_id']}/report-card?period=1", headers=ph).json()["average"] == "15.50")
check("the director's exports work", sco.get(f"/v1/exports/headcount?year_id={year}", headers=d).json()["total"]["total"] == (2 if roll_ok else 1) and sco.get(f"/v1/exports/headcount?year_id={year}&format=csv", headers=d).text.lstrip("\ufeff").startswith("Niveau;"))
print(f"\n{ok} passed, {fail} failed (part 2)")
sys.exit(1 if fail else 0)
