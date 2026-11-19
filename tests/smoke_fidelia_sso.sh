#!/usr/bin/env bash
# SMOKE (not e2e): the REAL fidelia-BE (SQLite, SSO on, throwaway container) next to the REAL auth service on the platform network.
# Needs infra + auth running (auth has a service key for fidelia). Fidelia is started from its repo checkout (current branch).
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FID="${FIDELIA_BACKEND_DIR:-$ROOT/../fidelia/fidelia-BE/backend-api}"
cd "$ROOT/infra"; set -a; source .env; source secrets/e2e_auth.env; set +a
SVC=$(grep '^AUTH_SERVICE_KEYS_JSON=' "$ROOT/auth/.env.platform" | grep -o '"fidelia": *"[^"]*"' | sed 's/.*: *"\(.*\)"/\1/')
docker rm -f fidelia-smoke >/dev/null 2>&1
docker run -d --name fidelia-smoke --network infra_net -v "$FID:/src:ro" -v "$ROOT/kit:/kit:ro" \
  -e FIDELIA_SECRET_KEY=smoke-only-jwt-secret -e DATABASE_URL=sqlite+aiosqlite:////tmp/smoke.db -e FIDELIA_SEED_SAMPLE=1 -e MOBILE_MONEY_SECRETS=dev-secret \
  -e MOBILE_MONEY_PROVIDER=fake -e CELERY_BROKER_URL=memory:// -e CELERY_RESULT_BACKEND=cache+memory:// -e PUBLIC_READ_RATE_LIMIT=100000/minute \
  -e FIDELIA_SSO_ENABLED=1 -e FIDELIA_SSO_ISSUER="http://auth-api.${DOMAIN}:${HTTP_PORT}" -e FIDELIA_SSO_JWKS_URL=http://auth-web:8000/.well-known/jwks.json \
  -e FIDELIA_SSO_AUTH_URL=http://auth-web:8000 -e FIDELIA_SSO_SERVICE_KEY="$SVC" -e PYTHONDONTWRITEBYTECODE=1 \
  python:3.11-slim-bookworm sh -c 'cp -r /src /work && cp -r /kit /kitwork && cd /work && for i in 1 2 3 4; do pip install -q --no-cache-dir -r requirements.txt -c requirements.lock "/kitwork[sso]" >/tmp/pip.log 2>&1 && break; sleep 3; done; exec uvicorn app.main:app --host 0.0.0.0 --port 8000' >/dev/null
echo -n "waiting for fidelia"; for i in $(seq 1 90); do docker exec fidelia-smoke python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/health')" >/dev/null 2>&1 && break; echo -n .; sleep 4; done; echo
CADDY_IP=$(docker inspect infra-caddy-1 --format '{{(index .NetworkSettings.Networks "infra_net").IPAddress}}')
docker run --rm --network infra_net --add-host "auth-api.${DOMAIN}:${CADDY_IP}" -e DOMAIN -e E2E_ADMIN_PHONE -e E2E_ADMIN_PASSWORD -e E2E_ADMIN_TOTP_SECRET \
  -v "$ROOT/tests/smoke_fidelia_sso.py:/smoke.py:ro" python:3.12-slim sh -c 'pip install -q --no-cache-dir httpx pyotp 2>&1 | grep -v -e notice -e WARNING; python /smoke.py'
rc=$?
docker rm -f fidelia-smoke >/dev/null 2>&1
exit $rc
