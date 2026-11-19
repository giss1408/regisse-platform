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
| 2b. flariane onto the platform (Dockerfile, compose.platform.yaml, db_flariane, Caddy route, Celery worker) | IN PROGRESS | |
| 2c. flariane regression: existing pytest suite on Postgres 17 + e2e (`backend/e2e/run_e2e.py`) through Caddy | TODO | |
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
