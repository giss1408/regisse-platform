#!/usr/bin/env bash
# SMOKE (not e2e): the REAL admin app API client (Dart, auth mode) against the local platform: sign in through auth + exchange on an
# approved device, unknown device stays pending, temporary password forced change, logout ends both sessions.
# Needs infra, auth, flariane (FLR_SSO_* set), the flariane e2e admin (bash tests/e2e_flariane.sh once) imported into auth
# (bash tests/smoke_admin_sso.sh imports it) and Flutter. Each scenario uses a new authenticator step, hence the waits.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/infra"; set -a; source .env; source secrets/e2e_auth.env; set +a
PORT="${HTTP_PORT:-8080}"; FLRSEC="$ROOT/infra/secrets/e2e_flariane.env"; STATE="$ROOT/infra/e2e-state/.state.json"
FLR_PW=$(grep "^E2E_ADMIN_PASSWORD=" "$FLRSEC" | cut -d= -f2-); FLR_TOTP=$(grep "^E2E_ADMIN_TOTP_SECRET=" "$FLRSEC" | cut -d= -f2-)
CADDY_IP=$(docker inspect infra-caddy-1 --format '{{(index .NetworkSettings.Networks "infra_net").IPAddress}}')
fail=0
for SC in ok pending forced; do
  for P in auth flariane; do IDX=$(grep -o '/[0-9]*$' "$ROOT/infra/secrets/$P.env" | head -1 | tr -d /)
    docker compose exec -T redis sh -c "redis-cli --no-auth-warning -a \"\$REDIS_ADMIN_PASSWORD\" -n ${IDX} --scan --pattern 'LIMITS*' | xargs -r -n 50 redis-cli --no-auth-warning -a \"\$REDIS_ADMIN_PASSWORD\" -n ${IDX} del >/dev/null" || true; done
  CFG=$(docker run --rm --network infra_net --add-host "auth-api.${DOMAIN}:${CADDY_IP}" -e DOMAIN -e PORT="$PORT" -e E2E_ADMIN_PHONE -e E2E_ADMIN_PASSWORD -e E2E_ADMIN_TOTP_SECRET \
    -e FLR_E2E_PASSWORD="$FLR_PW" -e FLR_E2E_TOTP="$FLR_TOTP" -e FLR_E2E_DEVICE="$(python3 -c "import json;print(json.dumps(json.load(open('$STATE'))))")" \
    -v "$ROOT/tests/smoke_admin_app_auth.py:/prep.py:ro" python:3.12-slim sh -c 'pip install -q --no-cache-dir httpx pyotp 2>&1 | grep -v -e notice -e WARNING >&2; python /prep.py '"$SC") || { echo "prep failed ($SC): $CFG"; exit 1; }
  echo "== scenario: $SC"
  (cd "$ROOT/../flariane/admin_app" && flutter test test/live/auth_live_smoke_test.dart --dart-define=LIVE_CFG="$CFG" --reporter expanded 2>&1 | grep -E "^\S* *\+[0-9]+.*(:|All)" | tail -4) || fail=1
  [ "$SC" != "forced" ] && sleep 31
done
exit $fail
