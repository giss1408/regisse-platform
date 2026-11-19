#!/usr/bin/env bash
# End-to-end run of the auth service through Caddy. Creates a dedicated e2e super admin once.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SECRET="$ROOT/infra/secrets/e2e_auth.env"
cd "$ROOT/infra"; set -a; source .env; set +a

if [ ! -f "$SECRET" ]; then
  PW="E2e-$(openssl rand -hex 10)-Pw"; PHONE="07$(printf '%08d' $((RANDOM*RANDOM % 100000000)))"
  out=$(docker exec -e AUTH_ADMIN_PASSWORD="$PW" auth-web python -m app.cli create-admin --phone "$PHONE" --name "E2E Admin" --role super_admin 2>&1) || { echo "create-admin failed: $out"; exit 1; }
  totp=$(echo "$out" | grep -o 'secret=[A-Z2-7]*' | tail -1 | cut -d= -f2)
  [ -n "$totp" ] || { echo "could not read TOTP secret: $out"; exit 1; }
  svc=$(grep '^AUTH_SERVICE_KEYS_JSON=' "$ROOT/auth/.env.platform" | grep -o '"flariane": *"[^"]*"' | sed 's/.*: *"\(.*\)"/\1/')
  umask 077; printf 'E2E_ADMIN_PHONE=%s\nE2E_ADMIN_PASSWORD=%s\nE2E_ADMIN_TOTP_SECRET=%s\nE2E_ADMIN_ROLE=super_admin\nE2E_SERVICE_KEY=%s\n' "$PHONE" "$PW" "$totp" "$svc" > "$SECRET"
fi

# Repeated runs from one IP trip real rate limits: clear only the limiter keys, not queues or data.
IDX=$(grep -o '/[0-9]*$' "$ROOT/infra/secrets/auth.env" | head -1 | tr -d /)
docker compose exec -T redis sh -c "redis-cli --no-auth-warning -a \"\$REDIS_ADMIN_PASSWORD\" -n ${IDX:-4} --scan --pattern 'LIMITS*' | xargs -r -n 50 redis-cli --no-auth-warning -a \"\$REDIS_ADMIN_PASSWORD\" -n ${IDX:-4} del >/dev/null" || true

CADDY_IP=$(docker inspect infra-caddy-1 --format '{{(index .NetworkSettings.Networks "infra_net").IPAddress}}')
docker run --rm --network infra_net --add-host "auth-api.${DOMAIN}:${CADDY_IP}" --env-file "$SECRET" \
  -e E2E_AUTH_URL="http://auth-api.${DOMAIN}" -e E2E_ISSUER="http://auth-api.${DOMAIN}:${HTTP_PORT}" -e PYTHONDONTWRITEBYTECODE=1 \
  -v "$ROOT/auth/e2e:/e2e:ro" -v "$ROOT/kit:/kit:ro" python:3.12-slim sh -c '
    cp -r /kit /kitwork && pip install -q --no-cache-dir httpx pyotp "/kitwork[sso]" 2>&1 | grep -v -e notice -e WARNING
    python /e2e/run_e2e.py'
