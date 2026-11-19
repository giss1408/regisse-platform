#!/usr/bin/env bash
# flariane's own end-to-end flow (backend/e2e/run_e2e.py) run against the platform stack through Caddy.
# Requires: infra up, flariane up (flariane/compose.platform.yaml). Creates a dedicated e2e SUPER_ADMIN once.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FLR="$ROOT/../flariane"
SECRET="$ROOT/infra/secrets/e2e_flariane.env"
cd "$ROOT/infra"; set -a; source .env; set +a

if [ ! -f "$SECRET" ]; then
  PW="E2e-$(openssl rand -hex 10)-Pw"
  out=$(docker exec -e FLR_ADMIN_PASSWORD="$PW" flariane-web python -m app.cli create-admin --email e2e@flariane.test --name "E2E" --role SUPER_ADMIN 2>&1) || { echo "create-admin failed: $out"; exit 1; }
  totp=$(echo "$out" | grep -o 'secret=[A-Z2-7]*' | head -1 | cut -d= -f2)
  [ -n "$totp" ] || { echo "could not read TOTP secret"; exit 1; }
  umask 077; printf 'E2E_ADMIN_EMAIL=e2e@flariane.test\nE2E_ADMIN_PASSWORD=%s\nE2E_ADMIN_TOTP_SECRET=%s\n' "$PW" "$totp" > "$SECRET"
fi

# Repeated runs from one IP trip flariane's real rate limits (sign-ups, password resets: per hour). Clear only the
# limiter keys (not the Celery queues) so every run starts clean. Local/test stack only.
IDX=$(grep -o '/[0-9]*$' "$ROOT/infra/secrets/flariane.env" | head -1 | tr -d /)
docker compose exec -T redis sh -c "redis-cli --no-auth-warning -a \"\$REDIS_ADMIN_PASSWORD\" -n ${IDX:-2} --scan --pattern 'LIMITS*' | xargs -r -n 50 redis-cli --no-auth-warning -a \"\$REDIS_ADMIN_PASSWORD\" -n ${IDX:-2} del >/dev/null" || true

CADDY_IP=$(docker inspect infra-caddy-1 --format '{{(index .NetworkSettings.Networks "infra_net").IPAddress}}')
run_e2e() {
  docker run --rm --network infra_net --add-host "flariane-api.${DOMAIN}:${CADDY_IP}" --env-file "$SECRET" \
    -e E2E_API_URL="http://flariane-api.${DOMAIN}" -e PYTHONDONTWRITEBYTECODE=1 \
    -v "$FLR/backend:/src:ro" -v "$ROOT/infra/e2e-state:/state" python:3.12-slim sh -c '
      mkdir -p /work && cp -r /src/. /work/ && cd /work && cp /state/.state.json e2e/.state.json 2>/dev/null
      pip install -q --no-cache-dir httpx pyotp pillow 2>&1 | grep -v notice
      python e2e/run_e2e.py; rc=$?
      cp e2e/.state.json /state/.state.json 2>/dev/null; exit $rc'
}
mkdir -p "$ROOT/infra/e2e-state"
out=$(run_e2e 2>&1); rc=$?
if echo "$out" | grep -q "waiting for approval"; then
  id=$(python3 -c "import json;print(json.load(open('$ROOT/infra/e2e-state/.state.json'))['device_id'])")
  docker exec flariane-web python -m app.cli approve-device --device-id "$id" >/dev/null
  out=$(run_e2e 2>&1); rc=$?
fi
echo "$out" | tail -${E2E_TAIL:-60}
exit $rc
