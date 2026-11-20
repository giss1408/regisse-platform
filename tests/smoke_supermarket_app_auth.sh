#!/usr/bin/env bash
# SMOKE (not e2e): the supermarket app's REAL Dart code (auth service client, session controller, interceptor) against the REAL platform:
# sign-up, WhatsApp code (a back-office token plays the team), sign-in, API call, temporary password, sign-out, account deletion.
# Needs infra, auth and the supermarket (SSO on) running, and Flutter. Reached at 127.0.0.1 with the Host header (Dart does not resolve *.localhost).
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/infra"; set -a; source .env; source secrets/e2e_auth.env; set +a
PORT="${HTTP_PORT:-8080}"
CADDY_IP=$(docker inspect infra-caddy-1 --format '{{(index .NetworkSettings.Networks "infra_net").IPAddress}}')
BOSS=$(docker run --rm --network infra_net --add-host "auth-api.${DOMAIN}:${CADDY_IP}" -e DOMAIN -e E2E_ADMIN_PHONE -e E2E_ADMIN_PASSWORD -e E2E_ADMIN_TOTP_SECRET \
  -v "$ROOT/tests/boss_token.py:/boss.py:ro" python:3.12-slim sh -c 'pip install -q --no-cache-dir httpx pyotp >/dev/null 2>&1; python /boss.py') || { echo "could not sign the back office in"; exit 1; }
CFG="{\"auth\":\"http://127.0.0.1:${PORT}\",\"auth_host\":\"auth-api.${DOMAIN}\",\"api\":\"http://127.0.0.1:${PORT}\",\"api_host\":\"supermarket-api.${DOMAIN}\",\"boss\":\"${BOSS}\"}"
cd "$ROOT/../supermarket-app/app"
flutter test test/live/auth_live_smoke_test.dart --dart-define=LIVE_CFG="$CFG" --reporter expanded 2>&1 | tail -15
