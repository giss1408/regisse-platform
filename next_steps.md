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

**Immediate next action (small):** make flariane e2e fully green. The 2 failing checks are:
- "public info: support WhatsApp number is set" -> set `support_whatsapp` in flariane's admin settings
- "public info: payment accounts offered" -> set a Wave number in settings
These are rows in flariane's settings (fresh `db_flariane` has none). Seed them (admin API or a seed command), rerun `platform/tests/e2e_flariane.sh`, expect 101/101. Then add a `seed` step to the flariane compose/README so a new environment has them.

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
