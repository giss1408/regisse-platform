#!/usr/bin/env bash
# SMOKE (not e2e): the REAL customer app API client (Dart, auth mode) against the local platform:
# login/refresh/logout through auth, flariane accepting the token, forced password change, sign-up + team relay + activation +
# referral, account deletion in the right order. Needs infra, auth and flariane (FLR_SSO_ENABLED=true) running; Flutter installed.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/infra"; set -a; source .env; source secrets/e2e_auth.env; set +a
PORT="${HTTP_PORT:-8080}"
# clear only rate-limit keys (several smoke runs from one IP)
for P in auth flariane; do IDX=$(grep -o '/[0-9]*$' "$ROOT/infra/secrets/$P.env" | head -1 | tr -d /)
  docker compose exec -T redis sh -c "redis-cli --no-auth-warning -a \"\$REDIS_ADMIN_PASSWORD\" -n ${IDX} --scan --pattern 'LIMITS*' | xargs -r -n 50 redis-cli --no-auth-warning -a \"\$REDIS_ADMIN_PASSWORD\" -n ${IDX} del >/dev/null" || true; done
CADDY_IP=$(docker inspect infra-caddy-1 --format '{{(index .NetworkSettings.Networks "infra_net").IPAddress}}')
CFG=$(docker run --rm --network infra_net --add-host "auth-api.${DOMAIN}:${CADDY_IP}" -e DOMAIN -e PORT="$PORT" -e E2E_ADMIN_PHONE -e E2E_ADMIN_PASSWORD -e E2E_ADMIN_TOTP_SECRET \
  -v "$ROOT/tests/smoke_customer_app_auth.py:/prep.py:ro" python:3.12-slim sh -c 'pip install -q --no-cache-dir httpx pyotp 2>&1 | grep -v -e notice -e WARNING >&2; python /prep.py') || { echo "prep failed: $CFG"; exit 1; }
cd "$ROOT/../flariane/customer_app"
flutter test test/live/auth_live_smoke_test.dart --dart-define=LIVE_CFG="$CFG" --reporter expanded 2>&1 | tail -25
