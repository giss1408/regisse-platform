#!/usr/bin/env bash
# LOCAL CHAIN RUN for scolaris on the platform (see e2e_scolaris.py). Needs infra, auth (with the scolaris product) and scolaris running.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/infra"; set -a; source .env; source secrets/e2e_auth.env; set +a
STATE=$(mktemp -d); chmod 777 "$STATE"; trap 'rm -rf "$STATE"' EXIT
CADDY_IP=$(docker inspect infra-caddy-1 --format '{{(index .NetworkSettings.Networks "infra_net").IPAddress}}')
export DIRECTOR_PHONE="07$(printf '%08d' $((RANDOM*RANDOM % 100000000)))"
run_py(){ docker run --rm --network infra_net --add-host "auth-api.${DOMAIN}:${CADDY_IP}" --add-host "scolaris-api.${DOMAIN}:${CADDY_IP}" -e DOMAIN -e DIRECTOR_PHONE -e E2E_ADMIN_PHONE -e E2E_ADMIN_PASSWORD -e E2E_ADMIN_TOTP_SECRET \
  -v "$ROOT/tests/e2e_scolaris.py:/run.py:ro" -v "$STATE:/state" python:3.12-slim sh -c "pip install -q --no-cache-dir httpx pyotp 2>&1 | grep -v -e notice -e WARNING; python /run.py $1"; }
fail=0
echo "== part 1: the platform admin is created at auth"; run_py 1 || fail=1
echo "== the platform team creates the school (enrols the director at auth)"
docker exec scolaris-web python -m app.cli create-school --name "Ecole E2E" --city Abidjan --director-name "Directrice E2E" --director-phone "$DIRECTOR_PHONE" --year 2026-2027 --starts 2026-09-14 --ends 2027-06-30 || fail=1
echo "== part 2: activation, sign-in at auth, scolaris"; run_py 2 || fail=1
echo "== part 3: the Flutter app's real Dart code against the live stack"
( cd "$ROOT/../scolaris/app" && SCOLARIS_LIVE=1 LIVE_PHONE="$DIRECTOR_PHONE" LIVE_PASSWORD="Ecole-Pass-2026x" LIVE_DOMAIN="$DOMAIN" flutter test integration/live_smoke_test.dart 2>&1 | tr '\r' '\n' | grep -E "All tests passed|Some tests failed|Expected|Actual|\[E\]" | sort -u ) | tee "$STATE/app.out"
grep -q "All tests passed" "$STATE/app.out" || fail=1
echo; [ "$fail" = 0 ] && echo "SCOLARIS CHAIN: all green" || echo "SCOLARIS CHAIN: FAILURES"
exit $fail
