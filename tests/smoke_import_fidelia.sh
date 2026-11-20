#!/usr/bin/env bash
# SMOKE (not e2e): fidelia's `users` table (a throwaway Postgres database shaped like fidelia's) imported into the running auth service:
# dry run, apply, apply again, then real sign-ins with the fidelia password, the admin that needs a code, and the SMS-era account.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/infra"; set -a; source .env; source secrets/e2e_auth.env; set +a
PORT="${HTTP_PORT:-8080}"; pass=0; failn=0
ok(){ echo "  PASS  $1"; pass=$((pass+1)); }; bad(){ echo "  FAIL  $1 ${2:-}"; failn=$((failn+1)); }
J(){ python3 -c "import sys,json;d=json.load(sys.stdin);print(d$1)" 2>/dev/null; }
aut(){ curl -s -H "Host: auth-api.${DOMAIN}" "$@"; }
PSQL(){ docker compose exec -T -e PGPASSWORD="$POSTGRES_PASSWORD" postgres psql -U postgres -d "$1" -qAtc "$2"; }
R(){ echo $((RANDOM*RANDOM % 100000000)); }
M="07$(printf '%08d' $(R))"; A="07$(printf '%08d' $(R))"; S="07$(printf '%08d' $(R))"; D="07$(printf '%08d' $(R))"
HASH=$(docker exec auth-web python -c "from regisse_kit.crypto import hash_password; print(hash_password('Fid-Import-2026'))")

PSQL postgres "DROP DATABASE IF EXISTS fidelia_src" >/dev/null; PSQL postgres "CREATE DATABASE fidelia_src" >/dev/null
PSQL fidelia_src "create table users (id serial primary key, phone_e164 varchar(16) not null unique, roles varchar(64) not null default 'customer', disabled boolean not null default false, created_at timestamp not null, last_login_at timestamp, password_hash varchar(255), must_change_password boolean not null default false, phone_verified_at timestamp, failed_logins int not null default 0, locked_until timestamp)"
# ids start at a random base: the import remembers fidelia ids (idempotent), so a rerun of this smoke must not reuse them
B=$((RANDOM*10 + 1000))
PSQL fidelia_src "insert into users (id, phone_e164, roles, disabled, created_at, password_hash, phone_verified_at) values
  ($((B+1)), '+225$M', 'customer,merchant', false, now(), '$HASH', now()),
  ($((B+2)), '+225$A', 'admin', false, now(), '$HASH', now()),
  ($((B+3)), '+225$S', 'customer', false, now(), null, null),
  ($((B+4)), '+225$D', 'customer', true, now(), '$HASH', now())"
SRC="postgresql://postgres:$POSTGRES_PASSWORD@postgres:5432/fidelia_src"
run(){ docker exec -e FIDELIA_DATABASE_URL="$SRC" auth-web python -m app.cli import-fidelia "$@"; }

BEFORE=$(PSQL db_auth "select count(*) from legacy_identities where product='fidelia'")
DRY=$(run); [ "$(echo "$DRY" | J "['created']")" = "4" ] && ok "dry run plans 4 accounts" || bad "dry run" "$DRY"
[ "$(PSQL db_auth "select count(*) from legacy_identities where product='fidelia'")" = "$BEFORE" ] && ok "dry run wrote nothing (mappings unchanged: $BEFORE)" || bad "dry run wrote something"
APP=$(run --apply); [ "$(echo "$APP" | J "['created']")" = "4" ] && [ "$(echo "$APP" | J "['admins_needing_totp_setup']")" = "1" ] && ok "apply: 4 created, 1 admin needs an authenticator setup" || bad "apply" "$APP"
AGAIN=$(run --apply); [ "$(echo "$AGAIN" | J "['created']")" = "0" ] && ok "second apply creates nothing" || bad "second apply" "$AGAIN"
echo "$APP$AGAIN" | grep -q "$M\|Fid-Import" && bad "output leaked a phone or password" || ok "output has no phone number or password"

login(){ aut -X POST "http://127.0.0.1:$PORT/v1/login" -H 'content-type: application/json' -d "{\"phone\":\"$1\",\"password\":\"Fid-Import-2026\",\"product\":\"fidelia\",\"app\":\"$2\"}"; }
T=$(login "$M" merchant); [ "$(echo "$T" | J "['role']")" = "merchant" ] && ok "merchant signs in with her FIDELIA password at the auth service" || bad "merchant login" "$T"
[ "$(login "$M" customer | J "['role']")" = "customer" ] && ok "...and as a customer too (two roles)" || bad "customer role"
PH=$(python3 -c "import sys,json,base64;t=sys.argv[1].split('.')[1];print(json.loads(base64.urlsafe_b64decode(t+'='*(-len(t)%4)))['ph'])" "$(echo "$T" | J "['access_token']")")
[ "$PH" = "tel:+225$M" ] && ok "token carries fidelia's phone key (tel:+225...)" || bad "ph claim" "$PH"
[ "$(login "$A" admin | J "['detail']")" = "totp_not_set_up" ] && ok "the imported admin cannot sign in until a super admin gives her a code" || bad "admin without code"
[ "$(login "$S" customer | J "['detail']")" = "invalid_credentials" ] && ok "the SMS-era account has no password yet (reset through the relay)" || bad "sms era"
[ "$(login "$D" customer | J "['detail']")" = "account_blocked" ] && ok "a disabled fidelia account stays disabled" || bad "disabled"

# --- fidelia erased a person: auth drops her access (service key, own product only), idempotent, staff refused
SVC=$(grep '^AUTH_SERVICE_KEYS_JSON=' "$ROOT/auth/.env.platform" | grep -o '"fidelia": *"[^"]*"' | sed 's/.*: *"\(.*\)"/\1/')
erase(){ aut -X POST "http://127.0.0.1:$PORT/v1/service/erase" -H 'content-type: application/json' -H "X-Service-Key: $1" -d "{\"phone\":\"$2\"}"; }
[ "$(erase wrong "$M" | J "['detail']")" = "service_key_required" ] && ok "erase refuses a wrong service key" || bad "erase with a wrong key"
[ "$(erase "$SVC" "$A" | J "['result']")" = "staff_refused" ] && ok "erase never touches an admin" || bad "erase of staff"
[ "$(erase "$SVC" "$M" | J "['result']")" = "erased" ] && ok "fidelia's key erases the merchant (fidelia was her only product)" || bad "erase"
[ "$(login "$M" merchant | J "['detail']")" != "" ] && ok "she can no longer sign in" || bad "still signs in after erase"
[ "$(erase "$SVC" "$M" | J "['result']")" = "unknown" ] && ok "erasing again is a no-op" || bad "second erase"

PSQL postgres "DROP DATABASE IF EXISTS fidelia_src" >/dev/null
echo; echo "result: $pass passed, $failn failed"; [ "$failn" -eq 0 ]
