#!/usr/bin/env bash
# LOCAL CHAIN RUN for the supermarket on the platform (see e2e_supermarket.py). Needs infra, auth and the supermarket
# (`docker compose -f compose.platform.yaml up` in supermarket-app, SSO on) running. Creates a few throwaway accounts.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/infra"; set -a; source .env; source secrets/e2e_auth.env; set +a
STATE=$(mktemp -d); chmod 777 "$STATE"; trap 'rm -rf "$STATE"' EXIT
CADDY_IP=$(docker inspect infra-caddy-1 --format '{{(index .NetworkSettings.Networks "infra_net").IPAddress}}')
run_py(){ docker run --rm --network infra_net --add-host "auth-api.${DOMAIN}:${CADDY_IP}" --add-host "supermarket-api.${DOMAIN}:${CADDY_IP}" -e DOMAIN -e E2E_ADMIN_PHONE -e E2E_ADMIN_PASSWORD -e E2E_ADMIN_TOTP_SECRET \
  -v "$ROOT/tests/$1:/run.py:ro" -v "$STATE:/state" python:3.12-slim sh -c "pip install -q --no-cache-dir httpx pyotp 2>&1 | grep -v -e notice -e WARNING; python /run.py"; }
fail=0
echo "== part 1: the platform admin is created at auth"
run_py e2e_supermarket.py || fail=1
ADMIN_ID=$(cat "$STATE/admin_id" 2>/dev/null)
echo "== linking her in the supermarket (staff are never created from a token)"
docker exec supermarket-web python manage.py link_sso_user --auth-user-id "$ADMIN_ID" --role platform_admin --name "Admin Marche" || fail=1
echo "== part 2: customer, dashboard, retailer enrolment, staff, deletion"
run_py e2e_supermarket_2.py || fail=1
echo; [ "$fail" = 0 ] && echo "SUPERMARKET CHAIN: all green" || echo "SUPERMARKET CHAIN: FAILURES"
exit $fail
