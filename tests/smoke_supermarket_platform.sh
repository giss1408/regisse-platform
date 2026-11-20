#!/usr/bin/env bash
# SMOKE (not e2e): the supermarket backend running on the shared platform (Postgres 17, Redis, Caddy) and the auth service's new endpoints
# for it (service staff, erase by user id). Needs infra, auth and the supermarket (`docker compose -f compose.platform.yaml up`) running.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/infra"; set -a; source .env; set +a
PORT="${HTTP_PORT:-8080}"; pass=0; failn=0
ok(){ echo "  PASS  $1"; pass=$((pass+1)); }; bad(){ echo "  FAIL  $1 ${2:-}"; failn=$((failn+1)); }
J(){ python3 -c "import sys,json;d=json.load(sys.stdin);print(d$1)" 2>/dev/null; }
smk(){ curl -s -H "Host: supermarket-api.${DOMAIN}" "$@"; }
aut(){ curl -s -H "Host: auth-api.${DOMAIN}" "$@"; }
B="http://127.0.0.1:$PORT"
SVC=$(grep '^AUTH_SERVICE_KEYS_JSON=' "$ROOT/auth/.env.platform" | python3 -c "import sys,json;print(json.loads(sys.stdin.read().split('=',1)[1])['supermarket'])")
R(){ echo $((RANDOM*RANDOM % 100000000)); }
EMAIL="smoke$(R)@example.com"; PH="07$(printf '%08d' $(R))"; PH2="07$(printf '%08d' $(R))"

[ "$(smk -o /dev/null -w '%{http_code}' $B/healthz)" = 200 ] && ok "healthz through the shared Caddy" || bad healthz
[ "$(smk $B/readyz | J "['status']")" = ok ] && ok "readyz: database and Redis cache answer" || bad readyz "$(smk $B/readyz)"
[ "$(smk -o /dev/null -w '%{http_code}' -X POST -H 'content-type: application/json' --data-binary @<(head -c 7000000 /dev/zero | tr '\0' 'a') $B/api/v1/auth/login)" = 413 ] && ok "a 7 MB body is refused by Caddy (limit 6 MB)" || bad "body limit"
[ "$(smk -o /dev/null -w '%{http_code}' $B/internal/x)" = 404 ] && ok "/internal/* is not published" || bad internal
REG=$(smk -X POST -H 'content-type: application/json' -d "{\"email\":\"$EMAIL\",\"password\":\"Smoke-pass-2026x\",\"name\":\"Smoke\"}" $B/api/v1/auth/register)
TOK=$(smk -X POST -H 'content-type: application/json' -d "{\"email\":\"$EMAIL\",\"password\":\"Smoke-pass-2026x\"}" $B/api/v1/auth/login | J "['access']")
[ "$(smk -H "Authorization: Bearer $TOK" $B/api/v1/me | J "['email']")" = "$EMAIL" ] && ok "the existing email sign-up, sign-in and /me work on the platform database" || bad "api flow" "$REG"
IDX=$(grep -o '/[0-9]*$' "$ROOT/infra/secrets/supermarket.env" | head -1 | tr -d /)
KEYS=$(docker compose exec -T redis sh -c "redis-cli --no-auth-warning -a \"\$REDIS_ADMIN_PASSWORD\" -n $IDX --scan --pattern '*'" | grep -c "supermarket")
[ "${KEYS:-0}" -ge 1 ] && ok "the throttle counters live in the product's Redis database ($KEYS keys)" || bad "redis keys"

# --- the auth service, with the product's own service key
ST=$(aut -X POST -H "X-Service-Key: $SVC" -H 'content-type: application/json' -d "{\"phone\":\"$PH\",\"display_name\":\"Caissière Smoke\",\"role\":\"retailer_staff\"}" $B/v1/service/staff)
UID_=$(echo "$ST" | J "['user_id']")
[ "$(echo "$ST" | J "['activation']")" = relay ] && ok "the product's backend enrols a retailer's staff member by phone (a WhatsApp activation code, like fidelia's merchants)" || bad "enrol" "$ST"
L=$(aut -X POST -H 'content-type: application/json' -d "{\"phone\":\"$PH\",\"password\":\"Any-password-2026x\",\"product\":\"supermarket\",\"app\":\"retailer\"}" $B/v1/login)
[ -n "$(echo "$L" | J "['detail']")" ] && [ -z "$(echo "$L" | J "['access_token']")" ] && ok "she cannot sign in before the team's code is typed (no password yet)" || bad "pending login" "$L"
[ "$(aut -X POST -H "X-Service-Key: $SVC" -H 'content-type: application/json' -d "{\"phone\":\"$PH2\",\"display_name\":\"Admin X\",\"role\":\"platform_admin\"}" -o /dev/null -w '%{http_code}' $B/v1/service/staff)" = 403 ] && ok "the product cannot enrol its own admins through the service key" || bad "admin creation"
[ "$(aut -X POST -H "X-Service-Key: $SVC" -H 'content-type: application/json' -d "{\"phone\":\"$PH\",\"display_name\":\"Again\",\"role\":\"retailer_staff\"}" $B/v1/service/staff | J "['detail']")" = phone_taken ] && ok "enrolling the same number again is refused" || bad "duplicate"
[ "$(aut -X DELETE -H "X-Service-Key: $SVC" $B/v1/service/staff/$UID_ | J "['result']")" = disabled ] && ok "removing the only role switches the (still pending) account off" || bad remove
[ "$(aut -X DELETE -H "X-Service-Key: wrong" -o /dev/null -w '%{http_code}' $B/v1/service/staff/$UID_)" = 403 ] && ok "a wrong service key is refused" || bad "wrong key"
echo; echo "result: $pass passed, $failn failed"; [ "$failn" -eq 0 ]
