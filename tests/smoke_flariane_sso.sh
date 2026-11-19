#!/usr/bin/env bash
# SMOKE (not e2e) for flariane SSO dual-accept through Caddy. See smoke_flariane_sso.py.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/infra"; set -a; source .env; source secrets/e2e_auth.env; set +a
CADDY_IP=$(docker inspect infra-caddy-1 --format '{{(index .NetworkSettings.Networks "infra_net").IPAddress}}')
docker run --rm --network infra_net --add-host "auth-api.${DOMAIN}:${CADDY_IP}" --add-host "flariane-api.${DOMAIN}:${CADDY_IP}" \
  -e E2E_ADMIN_PHONE -e E2E_ADMIN_PASSWORD -e E2E_ADMIN_TOTP_SECRET -e DOMAIN -e PYTHONDONTWRITEBYTECODE=1 \
  -v "$ROOT/tests/smoke_flariane_sso.py:/smoke.py:ro" python:3.12-slim sh -c \
  'pip install -q --no-cache-dir httpx pyotp 2>&1 | grep -v -e notice -e WARNING; python /smoke.py'
