#!/usr/bin/env bash
# SMOKE (not e2e): flariane back office through auth. Needs infra, auth, flariane (FLR_SSO_* set) running and the flariane e2e
# admin created (bash platform/tests/e2e_flariane.sh once). Imports flariane's admin_users into auth, then exercises the exchange.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/infra"; set -a; source .env; source secrets/e2e_auth.env; set +a
FLRSEC="$ROOT/infra/secrets/e2e_flariane.env"; STATE="$ROOT/infra/e2e-state/.state.json"
[ -f "$FLRSEC" ] && [ -f "$STATE" ] || { echo "run: bash platform/tests/e2e_flariane.sh   (creates the flariane e2e admin and its approved device)"; exit 1; }
FLR_URL=$(grep '^DATABASE_URL=' "$ROOT/infra/secrets/flariane.env" | cut -d= -f2-)
FLR_KEY=$(grep '^FLR_FIELD_ENCRYPTION_KEY=' "$ROOT/../flariane/.env.platform" | cut -d= -f2-)
run(){ docker exec -e FLARIANE_DATABASE_URL="$FLR_URL" -e FLARIANE_FIELD_ENCRYPTION_KEY="$FLR_KEY" auth-web python -m app.cli import-flariane-admins "$@"; }
echo "== import (dry run, apply, apply again)"
J(){ python3 -c "import sys,json;print(json.load(sys.stdin)$1)"; }
DRY=$(run); echo "  dry run: created=$(echo "$DRY" | J "['created']") errors=$(echo "$DRY" | J "['errors']")"
APP=$(run --apply); echo "  apply:   created=$(echo "$APP" | J "['created']") errors=$(echo "$APP" | J "['errors']")"
AGAIN=$(run --apply); echo "  again:   created=$(echo "$AGAIN" | J "['created']") skipped=$(echo "$AGAIN" | J "['skipped_already_imported']")"
echo "$APP$AGAIN" | grep -qi "totp_secret\|password_hash\|e2e@flariane" && echo "  FAIL  stats leaked a secret or an email" || echo "  PASS  stats contain no secret or email"
for P in auth flariane; do IDX=$(grep -o '/[0-9]*$' "$ROOT/infra/secrets/$P.env" | head -1 | tr -d /)
  docker compose exec -T redis sh -c "redis-cli --no-auth-warning -a \"\$REDIS_ADMIN_PASSWORD\" -n ${IDX} --scan --pattern 'LIMITS*' | xargs -r -n 50 redis-cli --no-auth-warning -a \"\$REDIS_ADMIN_PASSWORD\" -n ${IDX} del >/dev/null" || true; done
echo "== exchange"
CADDY_IP=$(docker inspect infra-caddy-1 --format '{{(index .NetworkSettings.Networks "infra_net").IPAddress}}')
FLR_PW=$(grep "^E2E_ADMIN_PASSWORD=" "$FLRSEC" | cut -d= -f2-); FLR_TOTP=$(grep "^E2E_ADMIN_TOTP_SECRET=" "$FLRSEC" | cut -d= -f2-)
docker run --rm --network infra_net --add-host "auth-api.${DOMAIN}:${CADDY_IP}" --add-host "flariane-api.${DOMAIN}:${CADDY_IP}" \
  -e DOMAIN -e E2E_ADMIN_PHONE -e E2E_ADMIN_PASSWORD -e E2E_ADMIN_TOTP_SECRET -e FLR_E2E_PASSWORD="$FLR_PW" -e FLR_E2E_TOTP="$FLR_TOTP" -e FLR_E2E_DEVICE="$(python3 -c "import json;print(json.dumps(json.load(open('$STATE'))))")" \
  -v "$ROOT/tests/smoke_admin_sso.py:/smoke.py:ro" python:3.12-slim sh -c 'pip install -q --no-cache-dir httpx pyotp 2>&1 | grep -v -e notice -e WARNING; python /smoke.py'
