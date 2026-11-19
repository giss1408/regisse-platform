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
| 2c. flariane regression: pytest on Postgres 17 + e2e through Caddy | DONE | pytest **155 passed** with the kit (PG17); e2e `tests/e2e_flariane.sh` **101/101** with the kit |
| 3. `regisse_kit` v0.1, extracted from flariane only | DONE | `platform/kit`: 38 tests (incl. flariane parity vectors); adopted in flariane via thin wrappers; flariane pytest 155/155 and e2e 101/101 after adoption |
| 3b. `auth` service v0.1 (`platform/auth`) | DONE as a service; flariane NOT yet a consumer | 23 unit/integration tests (PG17), e2e through Caddy 32/32, kit 49 tests incl. `regisse_kit.sso` |
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
Numbers (owner): first-week payout cap 50,000 FCFA on a new wallet; alert when one admin does 3 fast-path activations in a day. Reading to confirm: the cap is the total paid to the new wallet in its first 7 days, enforced by fidelia payout logic.
Still open: BCEAO/ARTCI check, email provider for fallback.

## Session result (2026-11-19, after "continue with the e2e settings and regisse_kit")

- e2e settings: `flariane/backend/e2e/run_e2e.py` now fills ONLY empty settings (support WhatsApp, Wave) with placeholders via `PUT /admin/settings` before the public-info checks; it never overwrites admin-entered values and refuses production hosts. `tests/e2e_flariane.sh` also clears only flariane's rate-limit keys (`LIMITS*`) in Redis before each run, because repeated runs from one IP trip the real sign-up and password-reset limits.
- `FLR_REGISTER_PER_IP_HOUR=1000` / `FLR_REGISTER_GLOBAL_HOUR=1000` are set in the LOCAL `flariane/.env.platform` only. Production must keep the defaults.
- Kit adopted in flariane (uncommitted in the flariane repo): `app/security/crypto.py` (wrapper, keeps `_key`), `integrations/whatsapp.py` (verify_signature), `config.py` (database URL validator), `services/customer_auth.py` (phone normalisation, password strength). `backend/Dockerfile` installs the kit from build context `kit` (compose `additional_contexts: kit: ../platform/kit`). Replace by a pinned git tag once the kit has a remote.
- Kit bug fixed vs flariane's original: `to_asyncpg_url` no longer mangles `sqlite:///` URLs.
- Verified: kit 38/38; flariane pytest 155/155 (PG17, kit installed); flariane e2e 101/101 through Caddy; infra smoke 27/27 earlier (not rerun after the kit work).

## Next (not started)
Step 3b `auth` service: sources = fidelia `core/otp.py`, `api/auth.py`, `api/account.py`, `services/otp_sender.py` and flariane password/TOTP/wa.me code, via `regisse_kit`; then flariane becomes its first consumer (rerun the flariane e2e). Needed from the owner before/while building: BCEAO/ARTCI check (before Phase 7), email provider for the fallback channel. Owner actions for production unchanged (Hetzner, Cloudflare + Namecheap DNS move, R2 + age key, Meta WhatsApp Business).

## Step 3b result: `auth` service v0.1 (2026-11-19)

Built in `platform/auth` (FastAPI, SQLAlchemy async, Alembic, Ed25519 JWT via JWKS, reuses `regisse_kit`):
register -> relay queue -> admin sends wa.me code -> activate -> login (phone + password; TOTP for staff roles) -> access token (EdDSA, 10 min, claims `sub` uuid, `ph`, `aud`, `role`, `roles`, `app`, `amr`, `auth_time`, `sid`, `mcp`) + rotating refresh with reuse detection -> logout; password reset via relay with forced-change temp password; admin: relay queue/send/reject, search, disable/enable, revoke sessions, grant/revoke memberships (staff roles super admin only); `POST /v1/introspect` with service keys; JWKS + `kid` rotation (`app.cli rotate-key`); audit log without secrets/full phones; privileged accounts never go through the relay; per-phone login lockout; TOTP replay protection. `regisse_kit.sso.SsoVerifier` (algorithm pinned to EdDSA, aud/iss mandatory, role mandatory, JWKS cache with 30 s refetch floor, `require_role`, `require_step_up`).
Verified: auth tests 23/23 (`cd platform/auth && make test`), auth e2e 32/32 (`bash platform/tests/e2e_auth.sh`, includes a product-side verification with kit.sso against the live JWKS), kit 49/49 (`cd platform/kit && make test`), infra smoke 27/27 rerun, flariane unchanged since its last green run (155 pytest, 101/101 e2e).
Deployed locally: product `auth` on infra (db_auth, redis db 4), `auth-api.localhost:8080`, container `auth-web`. Secrets in `platform/auth/.env.platform` and `infra/secrets/e2e_auth.env` (both gitignored).

### Known gaps (honest list, in priority order)
1. **No endpoint to create staff accounts** (admin/agent/operator): today only `python -m app.cli create-admin`. Plan says the super admin creates admins and agents: needs `POST /v1/admin/staff` (super admin only, returns the TOTP provisioning URI once; agents without TOTP per owner decision) and a password-set flow for them.
2. **Flariane is not a consumer yet.** Next big step: dual-accept in flariane (its own tokens + auth tokens, mapped by `ph`/phone hash), then migrate its customers/admins into `db_auth` with the legacy mapping table, then rerun flariane pytest + e2e. Flariane admins have device approval and TOTP of their own (to map to `super_admin/operator/viewer` memberships).
3. No per-IP limit on `/v1/activate` and `/v1/refresh` (activation is capped per code: 5 attempts; an attacker can burn a victim's attempts, which only forces a new relay code).
4. `/v1/introspect`: any product's service key can introspect any product's token (low risk, tighten to key-product = token `aud`).
5. Fallback channels (SMS via fidelia's sender class, email) and the WhatsApp Cloud API `OtpChannel` are not implemented; relay (wa.me) only, as decided. No `OtpChannel` interface yet.
6. No account-number-change/recovery flow, no account deletion/anonymisation, no consent-version management beyond a constant.
7. No wallet-change confirmation purpose yet (fidelia Phase 7: generic "confirmation code" request purpose in the relay).
8. Production hardening not done: Redis-backed limits are wired but untested at scale; signing-key rotation schedule/runbook; backups of `db_auth` are covered by the infra backup script but the encryption key backup (AUTH_FIELD_ENCRYPTION_KEY, AUTH_HASH_KEY) must be stored in a vault, losing them makes users unreadable.
9. Security review of `auth` (planned gate before real users and before Phase 7) not done; mutation-style sanity checks of the security tests not done.

## Step 3c: staff endpoint + flariane SSO dual-accept (2026-11-19, owner: branches in flariane/fidelia, smoke tests only for flariane)

**Branch rule (owner):** new implementation in flariane and fidelia always goes on a branch. flariane now: `platform/shared-infra-kit` (Docker, compose, kit adoption; commit 0ee07ea) and `platform/sso-dual-accept` (branched from it, committed locally, NOT pushed or merged). fidelia repos: untouched so far (fidelia-BE is on `infra/docker-cloudflare-hardening`, fidelia-docs has 5 uncommitted changes that are not mine).

**auth:** `POST /v1/admin/staff` (super admin only; returns temporary password once, authenticator secret only for TOTP roles; agents have none) and `POST /v1/admin/staff/{id}/reset`. Tests 29/29; e2e 38/38 (section F covers staff). Admins and super admins can deactivate agents (existing disable endpoint).

**flariane dual-accept (customers only):** flag `FLR_SSO_ENABLED` (default false), `FLR_SSO_ISSUER`, `FLR_SSO_JWKS_URL`, `FLR_SSO_AUDIENCE=flariane`. `app/security/sso.py` + hook in `security/deps.py:current_customer`. Customer matched by `ph` claim -> `phone_hash`; unknown phone gets a Customer on first use (phone_verified_at now, consent_version `sso-2026-11`, referral code). Admin routes and flariane's own login/refresh unchanged.
Verified (smoke, no e2e as requested): 17 new tests in `backend/tests/test_sso_dual_accept.py` (local Ed25519 key + JWKS; includes forged HS256/none/other-key, wrong aud/iss/exp, admin app/role, mcp, blocked customer, auth outage = 401); mutation check on a copy (guard removed) made exactly the 3 relevant tests fail; flariane full pytest **172 passed** (155 + 17); live smoke through Caddy `bash platform/tests/smoke_flariane_sso.sh` **5/5** (customer created in auth, relay-activated, calls flariane `/me` with the auth token; admin route, forged token and anonymous refused).
Found and fixed on the way: in `compose.platform.yaml` Celery beat could not write `celerybeat-schedule` as the non-root user and crashed silently, so flariane's scheduled tasks (expire quotes, cancel unpaid orders) were NOT running in the platform stack; schedule file now in `/tmp` (beat starts, worker healthy). Worth checking whether the same happened on Render (`render.production.yaml` runs the worker with `-B` as well).

### Not done / next
1. Flariane **admins** are not on SSO (device approval + TOTP model); decide mapping to `super_admin/operator/viewer` memberships.
2. **Migration of existing flariane customers into `db_auth`** (legacy mapping, password hashes are argon2id and importable) is not done: today an SSO user and a native flariane user with the same phone map to the same Customer row, but their passwords are separate (auth password vs flariane password).
3. Flariane's own login/register endpoints are still the primary path; switching the customer app to `auth` (and retiring flariane's customer refresh tokens) is a later, separate change.
4. Still open from before: BCEAO/ARTCI check, email fallback provider, SMS/email/WhatsApp-API channels, security review of `auth`, key backups to a vault.
5. fidelia: nothing built (Phase 7 gated).

## Step 3d: migrate flariane customers into auth (2026-11-19)

**Tool:** `python -m app.cli import-flariane [--apply]` (inside `auth-web`; dry run by default). Reads flariane's `customers` READ-ONLY (`FLARIANE_DATABASE_URL`), decrypts phones in memory with `FLARIANE_FIELD_ENCRYPTION_KEY` (both passed at run time, never stored in auth). Logic in `app/services/legacy_import.py` (pure, tested), new table `legacy_identities(product, legacy_id, user_id)` (migration 0002, up/down/up verified).
Rules: idempotent (mapped legacy ids skipped); a phone that already has an auth account is LINKED (membership + mapping), its password never overwritten; password hashes imported as they are (argon2id verifies, rehashed on first login if needed); blocked -> disabled; unverified -> pending_activation + open activation request in the relay queue; anonymised -> skipped; reset requested -> open password_reset request; temp password and consent carried over; sessions/refresh tokens are NOT migrated (users sign in again); a failing row never stops the others and stats never contain phones, hashes or secrets.
Verified: auth tests **36/36** (7 new in `tests/test_legacy_import.py`: login with the old password, status mapping, resets/temp passwords, idempotency, dry run = same plan with nothing written, linking without overwriting, bad row isolation and no leaks); live smoke `bash platform/tests/smoke_migrate_flariane.sh` **11/11** (a customer registered in flariane the normal way is migrated, logs in to auth with her OLD flariane password, and flariane maps the auth token to the SAME customer id with her name kept, no duplicate; her old flariane session still works).
Local data only: no production users exist, the real Render database has not been touched or imported.

### Risks to close before relying on this in production
1. **Two password stores drift.** After import, a password change or reset done in flariane does not reach auth (and the reverse). The import skips already-imported ids, so re-running does not refresh credentials. Fix at cutover: switch the customer app login/reset to auth at the same time (flariane stops being the credential authority), or add a `--refresh-credentials` mode and freeze changes during the window.
2. **Phone change in flariane breaks the link.** Dual-accept matches by phone hash. If a migrated customer changes her number in flariane (`PUT /me/phone`), her auth token still carries the old `ph`, so flariane would create a NEW customer. Recommended fix: put the legacy id in the token for migrated users (claim `lid` per product, from `legacy_identities`) and make flariane prefer `lid` over the phone; and make number change an auth event (plan 6d).
3. Run order for a real migration: stop signups in flariane (or accept a delta run: the importer is idempotent, so a second run picks up only new customers), dry run, review counts and errors, apply, verify with the smoke on a known account, then switch the app. Keep a `pg_dump` of both databases first (`infra/scripts/backup.sh`).
4. Unverified customers become `pending_activation` in auth with an open activation request: admins will see them in the auth relay queue (flariane's own unverified queue still exists until the app switches).

### Next options
(a) fix risk 2 (`lid` claim) and risk 1 (credential authority switch) so the customer app can log in through auth; (b) map flariane admins (devices + TOTP) to auth staff roles; (c) auth security review; (d) fallback channels (SMS/email/WhatsApp API).

## Step 3e: both migration risks fixed (2026-11-19)

**Risk 2 (phone change splits a customer): fixed with the `lid` claim.** auth puts `lid` = the user's id in the token's product (from `legacy_identities`) into access tokens of migrated users; `regisse_kit.sso.Principal.legacy_id`; flariane (branch `platform/sso-dual-accept`) resolves by `lid` first, refuses unknown/malformed ids (never provisions), falls back to the phone only for tokens without `lid`.
**Risk 1 (two password stores drift): fixed with refresh + authority + cutover.** `import-flariane --refresh --apply` pulls phone, password hash (+ forced change/temp password), name, blocking, late verification and ERASURE (flariane anonymisation erases the auth identity too) for accounts whose `credentials_authority` is "legacy". An account becomes "auth" when the user changes/resets her password in auth, or at `--cutover --apply` (final refresh, refuses to freeze if there are errors, idempotent). Refresh never touches an "auth" account, never re-enables a disabled one, never touches staff, and reports a phone already used by another auth account as an error instead of applying it. Migration 0003 (up/down/up verified).
Verified: auth unit/integration tests **45/45** (`cd platform/auth && make test`); kit **50/50**; flariane SSO smoke **20/20**; flariane full regression **175 passed**; live smoke `bash platform/tests/smoke_migrate_flariane.sh` **22/22** (migration, lid claim, number change keeps one customer, refresh moves the number to auth, password change in flariane reaches auth, a password set in auth is never overwritten, cutover freezes and is idempotent).

### Cutover runbook for flariane customers (when the customer app switches to auth login)
1. Backups: `infra/scripts/backup.sh` + dump of the flariane database. 2. Dry run `import-flariane` (+ `--refresh`), read counts/errors. 3. `--apply`, then `--refresh --apply` again (idempotent, picks up changes). 4. Verify with the smoke on a known account. 5. Ship the customer app build that logs in/resets through auth (not built yet). 6. `--cutover --apply` at the moment the old login stops being used. After step 6 flariane's own password endpoints must be disabled (not done: flariane still has them).

### Still open
Customer app does not log in through auth yet (Flutter change in `flariane/customer_app`, on a branch); flariane admins not on SSO; number change/recovery as an auth flow; SMS/email/WhatsApp-API channels; auth security review; key backups to a vault; BCEAO/ARTCI; email provider.

## Step 3f: customer app signs in through auth (2026-11-19)

Branch `platform/customer-app-auth-login` (flariane repo, from `platform/sso-dual-accept`; committed locally, not pushed). Build flag `--dart-define=AUTH_BASE_URL=<auth url>`: **empty (the default) keeps the legacy flariane login**, so the store build is unchanged until the cutover (then change the default to `https://auth-api.regisse.com`).
**App in auth mode:** register -> new ActivationScreen (the team sends the 6-digit code on WhatsApp via the auth relay) -> login with what she typed; login/refresh/logout/password reset/password change go to auth, everything else to flariane with the same access token; temporary password -> forced change screen using auth (flariane refuses `mcp` tokens, so no flariane call happens before it); delete account = password check in auth (`check_only`) -> flariane anonymise -> auth erase; phone change not available (message: write to the team); referral code typed at sign-up is applied after first login through the new `POST /me/referral`.
**Server additions:** auth `POST /v1/me/delete` (+ `check_only`), flariane `POST /me/referral` (once, before the first order; credits the signup point at once).
**Bug found and fixed:** the app's 401 retry ran through the same queued interceptors and deadlocked when the retried call failed again (e.g. a blocked customer holding a valid auth token): now retried on a plain client, and a still-refused fresh token signs her out. Regression test fails without the fix (checked).
Verified: Flutter analyze clean, **63 app tests + 22 new = all pass** (unit tests with fake servers, `flutter test`); live smoke with the REAL app client against the running stack `bash platform/tests/smoke_customer_app_auth.sh` **4/4** (login/refresh/logout; same error for wrong password and unknown number; temporary password -> forced change; sign-up + relay + activation + referral point credited to the referrer + delete in the right order); auth tests **47/47**; flariane backend regression **178 passed**; no on-device or store build run (not done here).

### Before the store release with auth
1. UI not tried on a device/emulator by me; run the app against local (`AUTH_BASE_URL=http://10.0.2.2:8080`, flariane `API_BASE_URL=http://10.0.2.2:8080`, hosts need the `Host` routing: use real hostnames or an adb reverse) and click through sign-up, forced change, forgot password, delete.
2. Certificate pinning: `pinned_roots.dart` covers the CA of flariane's host; `auth-api.regisse.com` (Cloudflare) must be covered too, check before shipping.
3. Production wiring: `auth` deployed with its own domain, `FLR_SSO_*` set on flariane, migrated customers (`import-flariane`, then `--cutover` when the app ships), flariane's own password endpoints disabled after the cutover.
4. Customers who already have the store app (legacy login) keep working until they update: flariane still accepts its own tokens. Plan the order: ship auth + import, then app release, then cutover freeze.
5. Number change and account recovery in auth (the app hides phone change in auth mode).

## Step 3g: flariane admins mapped to SSO (2026-11-19)

Branch `platform/admin-sso` in the flariane repo (from `platform/customer-app-auth-login`; committed locally, not pushed).

**Design.** Authentication (email, password, authenticator code, disable, role) moves to `auth`; what is flariane's own stays in flariane: device approval, its admin session (device-bound refresh), the AdminUser rows referenced by audit logs. The admin app signs in at auth, then exchanges the token at `POST /admin/auth/sso-login` with its device; the rest of the app keeps using flariane tokens.
**auth:** staff may log in with an **email** (new `email_enc/email_hash`), `totp_exempt` kept for migrated accounts that never had a code (never super_admin/admin), role `pickup` added, `lid` for staff lives in namespace `flariane.admin`, `POST /v1/sessions/check` (service key) tells a product if an auth session is alive, staff creation accepts email, user search by email. Migration 0004 (up/down/up verified).
**Import:** `python -m app.cli import-flariane-admins [--apply] [--refresh] [--cutover]` reads `admin_users` read-only, decrypts the authenticator secret with flariane's key IN MEMORY and re-encrypts it with auth's key, so **each admin keeps the same authenticator app entry**; password hashes as they are (argon2id); role -> membership; inactive -> disabled; devices are NOT migrated. Same rules as customers: idempotent, dry run, refresh only while credentials_authority is "legacy", cutover freezes, an email already used in auth is reported not merged, stats contain no secrets or emails.
**flariane:** `sso-login` refuses unless the token is `app=admin`, has `lid` of an ACTIVE flariane admin, is not `mcp`, and (authenticator code in `amr`, unless the account never had one and is not a super admin). Role is synced from auth. An unknown device is NEVER approved by signing in at auth (it waits for a super admin, as before). Refresh tokens remember `sso_sid` (migration a1b2c3d4e5f6); at each refresh flariane asks auth if the session is alive (`sso_session_ended` when not); `FLR_SSO_ADMIN_FAIL_OPEN` (default **true**) decides what happens when auth is unreachable; `FLR_SSO_ADMIN_ONLY=true` switches off the email/password admin login after the cutover. New env: `FLR_SSO_AUTH_URL`, `FLR_SSO_SERVICE_KEY` (local `.env.platform` has them).
**Verified:** auth tests **56/56**; flariane backend **199 passed** (21 new `tests/test_sso_admin.py` + the untouched admin auth tests after the login refactor); mutation check (TOTP requirement and `lid` requirement removed) made exactly the relevant tests fail; live smoke `bash platform/tests/smoke_admin_sso.sh` **12/12 + import checks** (import dry run/apply/again; sign in at auth with email + password + her EXISTING authenticator code; token role/amr/lid/no phone; exchange on an approved device; admin routes open; auth token itself refused on admin routes; unknown device stays pending; refresh while alive; auth disables her -> flariane ends the session at the next refresh; cleanup).

### Not done / decisions
1. **The admin app (Flutter `flariane/admin_app`) does not sign in through auth yet** (same kind of change as the customer app: login at auth, then `sso-login`; keep the device logic). Next step on its own branch.
2. **New back-office users created only in auth** cannot enter flariane yet: `sso-login` needs `lid` (a migrated admin). No automatic provisioning of AdminUser rows (kept manual on purpose: a super admin creates the flariane admin first, then it is imported/linked). A linking command or JIT creation for staff is open.
3. **Policy to review:** `totp_exempt` carries over flariane's "email + password only from an approved device" admins; the target decided for fidelia is a code for every back-office role. Consider removing the exemptions at the cutover.
4. **Disabling an admin in flariane's own panel** (`/admin/users/{id}/deactivate`) does not disable her in auth (until `--refresh` runs while credentials are still "legacy"; after cutover the admin panel's user management for these accounts must move to auth or call it).
5. **Fail-open default** at admin refresh (availability): a disabled admin can keep a flariane session through an auth outage, ending at the next refresh after auth is back. Set `FLR_SSO_ADMIN_FAIL_OPEN=false` for strict.
6. Cutover for admins (runbook): backups; dry run `import-flariane-admins`; apply; verify with the smoke; ship the admin app build; `--cutover --apply`; set `FLR_SSO_ADMIN_ONLY=true`.
7. Still open from before: auth security review, fallback channels (SMS/email/WhatsApp API), keys to a vault, BCEAO/ARTCI, email provider, certificate pinning for auth in the customer app, admin/customer app on-device checks.

## Step 3h: admin app signs in through auth (2026-11-19)

Branch `platform/admin-app-auth-login` (flariane repo, from `platform/admin-sso`; committed locally, not pushed). Build flag `--dart-define=AUTH_BASE_URL=<auth url>`; **empty keeps the legacy flariane login**. Same pattern as the customer app.
**Flow in auth mode:** email + password + authenticator code -> auth `/v1/login` (product flariane, app admin) -> `POST /admin/auth/sso-login` on flariane with this device (id + secret) -> flariane session as before (device-bound refresh, same screens). New `NewPasswordScreen` (SessionStatus.passwordChange) for a temporary password: change at auth, refresh, then the exchange. An **unknown device is never approved by signing in**: `device_pending` and no auth session is left open (auth logout). Logout ends the flariane session AND the auth session (auth refresh token kept in secure storage only for that). When auth ended the session (user disabled, logout elsewhere) flariane answers `sso_session_ended` at the next refresh and the app shows "Votre session a été fermée".
**Verified:** analyze clean; admin app tests **52 pass** (+3 live tests skipped without config): 12 new in `test/auth_admin_login_test.dart` (AdminApi two-step login with fake servers incl. no code, pending device, temporary password, error codes, legacy mode untouched; SessionController with auth: stores both refresh tokens, forced change, cancel, pending); mutation check (drop the auth logout for a pending device) made the right test fail; live smoke with the REAL AdminApi against the running stack `bash platform/tests/smoke_admin_app_auth.sh` **3/3 scenarios** (approved device signs in through auth and flariane opens the back office, refresh, logout ends both sessions; unknown device stays pending with no auth session; temporary password -> forced change -> sign-in continues, password restored for the next runs). The smoke needs the flariane e2e admin imported into auth (`bash platform/tests/smoke_admin_sso.sh` imports it).
Not run: on a device/emulator, a store build, the web admin panel build.

### Before the back-office cutover
1. Click through on an emulator: login, wrong code, locked account, temporary password, pending device, logout, session ended by auth.
2. Certificate pinning/CA for `auth-api.regisse.com` in the admin app if it pins (check `lib/core`; the customer app does).
3. Runbook (admins): backups; `import-flariane-admins` dry run, apply; verify with `smoke_admin_sso.sh`; ship the admin app build with `AUTH_BASE_URL`; `--cutover --apply`; `FLR_SSO_ADMIN_ONLY=true`.
4. Open decisions from step 3g still stand (new staff only in auth cannot enter flariane yet; `totp_exempt` policy; fail-open default; user management in flariane's panel vs auth).

## Step 4: auth security review (2026-11-19)

Report: `docs/platform/AUTH_SECURITY_REVIEW.md` (14 findings fixed with tests, residual risks, and findings outside auth). auth suite **70/70**, kit unchanged, live e2e 38/38 and flariane SSO smoke 5/5 after the changes; each fix mutation-checked (fix removed on a copy -> matching test fails). **Most urgent follow-ups outside auth:** flariane's pinned dependencies (pillow 71 known vulns, starlette, cryptography, PyJWT, python-multipart, requests) and fidelia's `python-jose`/`ecdsa`/`pyasn1`; both need their own branch + upgrade + regression run. Auth residual risk 1 (key rotation envelope) must be done before real users.

## Step 5: fidelia integration, first slice (2026-11-19)

Branch `platform/sso-dual-accept` in **fidelia-BE** (from `infra/docker-cloudflare-hardening`; committed locally, not pushed; no other fidelia repo touched).
**Built (backend-api):** `app/core/sso.py` (flag `FIDELIA_SSO_ENABLED`, off by default; issuer/JWKS/audience env; `ph` claim -> fidelia's phone-key identity so its ~100 phone-keyed call sites are untouched; auth role -> fidelia role, `super_admin` -> `admin`; role-less/`mcp`/wrong aud/iss/forged (HS256 with fidelia's own secret too, `none`) refused; any verification failure is a clean 401); `security.require_step_up` on PUT/DELETE `/merchant/wave`, POST/DELETE `/merchant/staff`, both CSV exports, and the admin role actions (grant, revoke, disable, revoke-sessions): for auth sign-ins only, a recent proof (5 min: password for shop staff, authenticator code for admins) + live session check at auth, **fail closed (503)** when auth is unreachable (`FIDELIA_SSO_MONEY_FAIL_CLOSED=0` to relax); legacy tokens unchanged; `FIDELIA_REJECT_ROLELESS=1` ends the "no role = merchant" default; **agents cannot set the wallet at enrolment** (`wallet_set_by_merchant`), admins still can.
**auth additions:** `POST /v1/step-up` (password, plus authenticator code for roles that need one -> fresh token, same session, fresh `auth_time`, replay protection), `service_caller` key binding (fidelia key added to the local auth env), kit now supports Python 3.11 (fidelia runs 3.11).
**Verified:** fidelia suite **373 passed, 5 skipped** (baseline before my change: 348 passed, 5 skipped; +25 new in `tests/test_sso.py`); auth step-up tests 4/4 (full auth suite was 70/70 before them); live smoke `bash platform/tests/smoke_fidelia_sso.sh` **11/11** (the real fidelia-BE in a throwaway container next to the real auth: customer and merchant tokens accepted, tampered refused, shop found by phone key, wallet route guard, auth step-up, logout at auth refuses the unexpired token on money routes, ordinary routes keep working until expiry by design).

### NOT done in fidelia (still the plan's Phase 7)
1. **Wallet change process** (merchant requests with password re-entry -> admin sends the WhatsApp confirmation code -> 24 h delay, "this wasn't me", admin call-and-activate, first-week cap 50,000 FCFA): not built. Needs an auth purpose for confirmation codes plus fidelia payout logic.
2. **Enrolment through auth** (agent creates the shop -> merchant identity pending in auth -> admin relay code -> merchant sets password): not built. Today fidelia still enrols the shop itself and the merchant signs in with fidelia's own SMS login; SSO merchants work only after an auth admin grants the role.
3. **Fidelia apps** (admin, retailer/Fidelia Pro, user) do not sign in through auth; all keep fidelia's own login. Each needs its own branch (as the flariane apps).
4. Admin relay UI for fidelia (queue of activations/resets) lives in auth's admin API only; no screen yet.
5. Fidelia image must get `regisse-kit[sso]`: the kit has no published package yet (Dockerfile untouched; tests and the smoke mount the kit). Publish it as a private package/git tag first.
6. Existing fidelia roles in its `users` table are not synced with auth memberships (auth is the authority for SSO tokens; legacy sessions use fidelia's table).
7. `python-jose`/`ecdsa`/`pyasn1`/`graphql-core`/`opentelemetry-api` have known vulnerabilities (see AUTH_SECURITY_REVIEW.md): replace python-jose with PyJWT on its own branch.
8. BCEAO/ARTCI check, email provider, owner decisions from steps 3g/3h unchanged.
