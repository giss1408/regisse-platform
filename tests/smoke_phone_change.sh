#!/usr/bin/env bash
# SMOKE (not e2e): the number change against the running auth service. Needs infra + auth up.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/infra"; set -a; source .env; source secrets/e2e_auth.env; set +a
PORT="${HTTP_PORT:-8080}"
IDX=$(grep -o '/[0-9]*$' "$ROOT/infra/secrets/auth.env" | head -1 | tr -d /)
docker compose exec -T redis sh -c "redis-cli --no-auth-warning -a \"\$REDIS_ADMIN_PASSWORD\" -n ${IDX} --scan --pattern 'LIMITS*' | xargs -r -n 50 redis-cli --no-auth-warning -a \"\$REDIS_ADMIN_PASSWORD\" -n ${IDX} del >/dev/null" 2>/dev/null
CADDY_IP=$(docker inspect infra-caddy-1 --format '{{(index .NetworkSettings.Networks "infra_net").IPAddress}}')
docker run --rm --network infra_net --add-host "auth-api.${DOMAIN}:${CADDY_IP}" -e DOMAIN -e PORT="$PORT" -e E2E_ADMIN_PHONE -e E2E_ADMIN_PASSWORD -e E2E_ADMIN_TOTP_SECRET \
  -v "$ROOT/tests/smoke_phone_change.py:/s.py:ro" python:3.12-slim sh -c 'pip install -q --no-cache-dir httpx pyotp 2>&1 | grep -v -e notice -e WARNING >&2; python /s.py'
