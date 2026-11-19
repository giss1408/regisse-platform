# next_steps.md (resume file for the platform migration)

Plan: `../docs/platform/MIGRATION_PLAN.md` · Inventory: `../docs/platform/INVENTORY.md` · Skill: `~/.claude/skills/regisse-platform`
Rule from owner: **no app has production users now** (2026-11-19), so cutovers need no rehearsal windows; still keep each step small, smoke-tested, e2e at the end. Update this file after every step.

## SCOPE CHANGE (owner, 2026-11-19): tests and migration concentrate on **flariane only**
The other apps are not finished: do NOT migrate or add tests for them until the owner says so. SuiviProjet was used once to validate the product template (pilot smoke 9/9 passed, left in place, container stopped); it is no longer in the test scope. SSO "all products" stays the target design, but the `auth` service is built and proven with flariane as the first and only consumer.

## Status

| Step | State | Evidence |
|---|---|---|
| 1. Local infra stack (`platform/infra`) | DONE | `make smoke` 27/27 |
| 2. Product template (`platform/template`) validated by a pilot | DONE (SuiviProjet, out of scope now) | `tests/pilot_suivi.sh` 9/9 |
| 2b. flariane onto the platform (Dockerfile, compose.platform.yaml, db_flariane, Caddy route, Celery worker) | DONE | web + worker healthy behind Caddy at `flariane-api.localhost:8080` |
| 2c. flariane regression: pytest on Postgres 17 + e2e through Caddy | MOSTLY DONE | pytest: **155 passed** (PG17). e2e (`tests/e2e_flariane.sh`): **99/101**; the 2 failures are unset app settings on the fresh DB (see below), not code |
| 3. `regisse_kit` v0.1, extracted from flariane only | TODO | |
| 3b. `auth` service (flariane wa.me pattern, JWKS, memberships); flariane is the only consumer, then its e2e again | TODO | |
| 4-5. Other products | PARKED (not finished by owner) | |
| 6. Final e2e (flariane through infra + auth) | TODO | |
| 1b. Hetzner + Cloudflare + R2 | TODO (owner accounts) | |

## How to run what exists

```
cd platform/infra
make init          # creates .env with generated secrets (local defaults: DOMAIN=localhost, HTTP_PORT=8080)
make up            # caddy + postgis17 + redis, network infra_net
make new-product NAME=foo [POSTGIS=1] [UPSTREAM=foo-web:8000]   # db_foo, role_foo, redis user, caddy route, secrets/foo.env
make smoke         # e2e smoke of the infra with throwaway products (cleans up after itself)
make backup / make restore-test
scripts/remove-product.sh <name> --yes   # DESTRUCTIVE
```

## Decisions made while implementing
- Product names: `^[a-z][a-z0-9]{1,24}$` (used in role/db/host names). Host pattern `<name>-api.<DOMAIN>`.
- Redis: one ACL user per product + own DB index (max 15 products), `-@dangerous -@admin`. Default user password-protected by `make up`. Celery-friendly (no key-prefix restriction).
- Caddy active health check on `/healthz` every 5 s: a product started after its route is created is 503 for up to 5 s. Every product MUST expose `/healthz`.
- Local TLS off (`CADDY_GLOBAL_OPTS="auto_https off"`, `SITE_SCHEME=http://`); production: empty SITE_SCHEME, Cloudflare Full (strict).

## Owner actions (blocking only production, not local work)
1. Hetzner Cloud project + API token or console access; staging first.
2. Cloudflare: add regisse.com, copy Namecheap DNS (esp. MX/SPF/DKIM) BEFORE switching nameservers.
3. Cloudflare R2 bucket + write-only API token (backup job) + `age` keypair (keep private key offline).
4. Meta Business verification for WhatsApp Business API (temporary: wa.me manual flow like flariane).

## Known gaps / TODO in infra
- Backup container/cron not defined (script exists; schedule on the host with cron or a sidecar when deploying).
- No monitoring/uptime yet (Phase 6).
- Postgres tuning defaults in `.env.example` are LOCAL-sized; production values in plan section 8 Phase 1.

## Where we stopped (owner said: stop after the e2e step, 2026-11-19)

Done and verified this session: infra smoke 27/27; flariane pytest 155/155 on Postgres 17; flariane e2e 99/101 through Caddy.

**Immediate next action (small):** the 2 e2e failures are expected on a fresh database, not a defect and not something to seed in the platform. Per owner, the support WhatsApp number and the Wave payment number are entered from the admin app (settings), so a new `db_flariane` has none until an admin sets them:
- "public info: support WhatsApp number is set" -> `support_whatsapp` setting
- "public info: payment accounts offered" -> Wave number setting
Options (owner to choose): (a) leave as is and treat 99/101 as the baseline for an unconfigured environment, (b) make `backend/e2e/run_e2e.py` set both through the admin settings API as a precondition (the same call the admin app makes), so a fresh environment reaches 101/101. Platform rule meanwhile: **never copy these values from Render or hardcode them in the platform; they stay admin-managed data** (a DB restore carries them, a new empty DB does not, so a restore/migration of the live flariane DB must be followed by checking both settings in the admin app).

**Changes made in the flariane repo (NOT committed there yet, review and commit):**
- `backend/Dockerfile`, `backend/.dockerignore`, `compose.platform.yaml` (new)
- `backend/app/main.py`: added `/healthz` alias next to `/health`
- `backend/requirements.txt`: added `kombu==5.4.2` (fixes a real crash: newer kombu needs redis-py >= 6.1 while flariane pins 5.2.1; the Celery worker could not start without it, probably also on Render)
- `.gitignore`: `.env.platform`; `.env.platform` itself holds generated local keys (not committed)

**Changes made in SuiviProjet (pilot, out of scope now):** `compose.platform.yaml`, `Makefile.platform`, `.env.platform`, and the `//# sourceMappingURL=` lines removed from `static/vendor/*.js` (fixed a pre-existing Docker build failure with whitenoise manifest storage). Container is stopped; `suivi` product still provisioned in infra (remove with `scripts/remove-product.sh suivi --yes` if unwanted).

**Running containers to know about:** infra (caddy, postgres, redis), `flariane-web`, `flariane-worker`, and a leftover test DB `flr-testdb` (safe to `docker rm -f flr-testdb`).

**Then, in order (not started):** Step 3 `regisse_kit` v0.1 (extract from flariane), Step 3b `auth` service (flariane wa.me pattern, JWKS, memberships; flariane sole consumer; rerun flariane e2e), Step 6 final e2e. Hetzner/Cloudflare/R2/Meta remain owner actions.

## Test commands (flariane scope)
```
cd platform/infra && make up && make smoke          # infra, 27 checks
cd flariane && docker compose -f compose.platform.yaml up -d --build --wait
bash platform/tests/e2e_flariane.sh                  # flariane e2e through Caddy (creates e2e admin once)
# flariane pytest on PG17: see flariane/backend/tests, needs a postgres on localhost:5433 (flariane/flariane);
# run used: docker run -d --name flr-testdb ... postgres:17-alpine -c port=5433, pytest in python:3.12-slim with --network container:flr-testdb
```

## Fidelia and SSO (owner decision 2026-11-19, read-only analysis done)

Plan section 6d records it. Nothing in fidelia was changed (only read: `api/auth.py`, `api/account.py`, `core/security.py`, `core/otp.py`, `services/otp_sender.py`, `api/staff.py`, `models.py`).

Key facts: fidelia logs in with **phone + SMS code on every sign-in** (Orange / Africa's Talking, budget + caps), JWT HS256 with one secret `FIDELIA_SECRET_KEY`, `sub` = `tel:+225...`, rotating refresh with theft detection, roles per app session, data keyed by phone key (~65 + ~40 call sites). Findings to fix, not copy: single shared secret, role-less token defaults to merchant, 60 min access TTL with no revocation check.

Consequences already written in the plan: `auth` is extracted mainly from **fidelia's** auth code (plus flariane's password/TOTP/wa.me), tokens carry `sub` UUID + `ph` phone key + `auth_time`/`amr`, money routes need step-up and revocation check, Phase 7 (fidelia joins last, behind flags, staff -> merchants -> customers).

**Owner decisions still open** (plan section 10, items 8-10):
8. Login model in the one user pool: SMS-code (fidelia) vs password (flariane); my lean = support both per user.
9. Keep fidelia's phone keys via the `ph` claim for now, UUID migration later (lean: yes).
10. BCEAO / ARTCI compliance check before Phase 7.

### Update (owner, same day): fidelia login follows the flariane model
A Fidelia member (agent/admin) registers the merchant in the admin app (shop, location, phone); the code reaches the merchant by WhatsApp from the admin app (wa.me link, server-built); merchant sets a password, then phone + password. Business API replaces the relay later. Written as plan section 6e; decision 8 closed.
Open, need the owner: 11 step-up factor for money actions (proposal: TOTP for owners/agents/admins), 12 customers of fidelia-App-user (proposal: keep automatic SMS code until Business API is live), 9 fidelia phone keys via `ph` claim (lean yes), 10 compliance check.

### Fidelia enrolment fields (owner, final)
Agent creates the shop with: category, name, commune, address, opening hours, shop phone and the merchant's own phone (login; mandatory for agents). **No wallet at creation**: the merchant owner enters their own wallet in Fidelia Pro after activation (password + TOTP step-up). No wallet = listed + loyalty points, no pay QR. Phase 7 work in fidelia: `admin_create_venue` must reject payout fields for the `agent` role; admin app form drops the wallet fields. (The earlier "unconfirmed wallet" idea is superseded.)

### Fidelia customers and fallbacks (owner, final)
No SMS login: end customers also follow the flariane way (phone + password, one-time code relayed by a Fidelia member via wa.me, Business API later). **SMS and email are fallbacks for everyone** (number not on WhatsApp, relay queue over SLA, user request). New work: an `Email` OtpChannel (provider to choose); reuse fidelia's Orange/Africa's Talking sender class as the SMS fallback with its caps and budget. Decision 12 closed. Still open: 11 (TOTP step-up for owners/agents/admins), 9 (phone keys via `ph` claim), 10 (BCEAO/ARTCI check), plus the relay daily cap review at 200/day.

### Fidelia roles (owner answers)
Admin and super admin use an authenticator code (TOTP); agents are created by the super admin. "Owner" was loose wording for the **`merchant` role** (shop owner/manager). Role table is in plan 6e: super_admin (new, created in person, 2+ recommended), admin, agent (super-admin-created, TOTP at login proposed because agents relay codes), merchant (TOTP only as step-up for wallet/payout settings), cashier (added by merchant, no TOTP), customer.
Open: (a) TOTP at login for agents (proposal yes), (b) fidelia phone keys via `ph` claim, (c) BCEAO/ARTCI check.

### Fidelia relay permissions (owner, final)
Only `admin` and `super_admin` send activation/reset codes (relay queue in the admin app). Agents only enrol shops (pending until an admin sends the code, which doubles as review of the agent's data) and can be deactivated at any time by an admin or super admin (disable + session revoke). Agents need no TOTP; admin/super_admin use TOTP at login. Plan 6e role table and controls updated; decision 11 closed except the merchant wallet step-up (TOTP), still a proposal.
Open: merchant TOTP step-up for wallet changes (confirm), fidelia phone keys via `ph` claim, BCEAO/ARTCI check, email provider for the fallback channel.

### Wallet entry/change process (owner, final; plan 6e)
No authenticator app for merchants. Merchant requests the wallet in Fidelia Pro (password re-entry) -> request waits in the admin queue -> an admin/super admin sends the WhatsApp confirmation code (wa.me, like login) to the merchant's login number -> merchant enters the code -> 24 h delay, then the new wallet activates. "This wasn't me" button freezes the change and locks the account for admin review. Admin fast path: the admin may call the merchant and activate immediately (call note mandatory, audited, alert on unusual volume, super admin can revert). All changes visible in the admin app. First-week payout cap on a new wallet applies even after immediate activation. First wallet = same process without the 24 h delay (my call). SSO assigns a UUID per account in the background; fidelia keeps phone keys via `ph` claim for now.
Still open: payout cap amount, N for the admin fast-path alert, BCEAO/ARTCI check, email provider for fallback.
