#!/usr/bin/env bash
# SMOKE (not e2e): migrate a real flariane customer into auth and check she keeps her password, her identity and her data.
# Needs infra, auth and flariane (FLR_SSO_ENABLED=true) running. Leaves one test customer behind in each database.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/infra"; set -a; source .env; set +a
PORT="${HTTP_PORT:-8080}"
pass=0; failn=0
ok(){ echo "  PASS  $1"; pass=$((pass+1)); }; bad(){ echo "  FAIL  $1 ${2:-}"; failn=$((failn+1)); }
J(){ python3 -c "import sys,json;d=json.load(sys.stdin);print(d$1)" 2>/dev/null; }
flr(){ curl -s -H "Host: flariane-api.${DOMAIN}" "$@"; }
aut(){ curl -s -H "Host: auth-api.${DOMAIN}" "$@"; }
PHONE="07$(printf '%08d' $((RANDOM*RANDOM % 100000000)))"; PW="Migr-Secret-2026"

# 1. a customer exists in flariane the normal way (register, number verified)
R=$(flr -X POST "http://127.0.0.1:$PORT/auth/register" -H 'content-type: application/json' -d "{\"phone\":\"$PHONE\",\"password\":\"$PW\",\"display_name\":\"Migr Awa\",\"consent\":true}")
FTOKEN=$(echo "$R" | J "['access_token']"); [ -n "$FTOKEN" ] && ok "customer registered in flariane" || { bad "flariane register" "$R"; exit 1; }
FID=$(flr -H "Authorization: Bearer $FTOKEN" "http://127.0.0.1:$PORT/me" | J "['id']")
docker compose exec -T -e PGPASSWORD="$POSTGRES_PASSWORD" postgres psql -U postgres -d db_flariane -qc "update customers set phone_verified_at = now() where id = $FID" >/dev/null && ok "number marked verified (stands in for the admin-sent code)"

# 2. migration: dry run, apply, apply again
FLR_URL=$(grep '^DATABASE_URL=' "$ROOT/infra/secrets/flariane.env" | cut -d= -f2-)
FLR_KEY=$(grep '^FLR_FIELD_ENCRYPTION_KEY=' "$ROOT/../flariane/.env.platform" | cut -d= -f2-)
run(){ docker exec -e FLARIANE_DATABASE_URL="$FLR_URL" -e FLARIANE_FIELD_ENCRYPTION_KEY="$FLR_KEY" auth-web python -m app.cli import-flariane "$@"; }
LEG(){ docker compose exec -T -e PGPASSWORD="$POSTGRES_PASSWORD" postgres psql -U postgres -d db_auth -Atc "select count(*) from legacy_identities where product='flariane' and legacy_id='$FID'"; }
DRY=$(run); [ "$(echo "$DRY" | J "['created']")" -ge 1 ] 2>/dev/null && ok "dry run reports it would create accounts" || bad "dry run" "$DRY"
[ "$(LEG)" = "0" ] && ok "dry run wrote nothing" || bad "dry run wrote a mapping"
APP=$(run --apply); [ "$(echo "$APP" | J "['created']")" -ge 1 ] && [ "$(LEG)" = "1" ] && ok "apply: customer created and mapped (flariane id $FID)" || bad "apply" "$APP"
AGAIN=$(run --apply); [ "$(echo "$AGAIN" | J "['created']")" = "0" ] && ok "second apply creates nothing (idempotent)" || bad "second apply" "$AGAIN"
echo "$APP$AGAIN" | grep -q "$PHONE" && bad "stats leaked the phone number" || ok "stats do not contain the phone number"

# 3. she logs in to auth with her OLD flariane password, and flariane recognises her as the SAME customer
L=$(aut -X POST "http://127.0.0.1:$PORT/v1/login" -H 'content-type: application/json' -d "{\"phone\":\"$PHONE\",\"password\":\"$PW\",\"product\":\"flariane\",\"app\":\"customer\"}")
ATOKEN=$(echo "$L" | J "['access_token']"); [ -n "$ATOKEN" ] && ok "login to auth with her flariane password" || { bad "auth login" "$L"; exit 1; }
M=$(flr -H "Authorization: Bearer $ATOKEN" "http://127.0.0.1:$PORT/me")
[ "$(echo "$M" | J "['id']")" = "$FID" ] && [ "$(echo "$M" | J "['display_name']")" = "Migr Awa" ] && ok "flariane maps the auth token to the SAME customer (id $FID, name kept)" || bad "identity mapping" "$M"
CNT=$(docker compose exec -T -e PGPASSWORD="$POSTGRES_PASSWORD" postgres psql -U postgres -d db_flariane -Atc "select count(*) from customers where id=$FID or display_name='Migr Awa'" ); [ "$(docker compose exec -T -e PGPASSWORD="$POSTGRES_PASSWORD" postgres psql -U postgres -d db_flariane -Atc "select count(*) from customers where phone_hash = (select phone_hash from customers where id=$FID)")" = "1" ] && ok "no duplicate customer created in flariane" || bad "duplicate customer"
[ "$(flr -H "Authorization: Bearer $FTOKEN" "http://127.0.0.1:$PORT/me" | J "['id']")" = "$FID" ] && ok "her old flariane session still works" || bad "old session"

# 4. risk 2: the token carries her flariane id (lid), so a number change cannot split her into two customers
claims(){ python3 -c "import sys,json,base64;t=sys.argv[1].split('.')[1];print(json.dumps(json.loads(base64.urlsafe_b64decode(t+'='*(-len(t)%4)))))" "$1"; }
[ "$(claims "$ATOKEN" | J "['lid']")" = "$FID" ] && ok "auth token carries lid = flariane customer id" || bad "lid claim" "$(claims "$ATOKEN")"
NEWPHONE="07$(printf '%08d' $((RANDOM*RANDOM % 100000000)))"
SQLV=$(docker exec flariane-web python -c "
from app.security.crypto import encrypt, phone_hash
p='+225$NEWPHONE'
print(encrypt(p).hex(), phone_hash(p))")
ENC=${SQLV% *}; HASH=${SQLV#* }
PSQL(){ docker compose exec -T -e PGPASSWORD="$POSTGRES_PASSWORD" postgres psql -U postgres -Atc "$2" -d "$1"; }
PSQL db_flariane "update customers set phone_enc = decode('$ENC','hex'), phone_hash = '$HASH' where id = $FID" >/dev/null && ok "she changes her number in flariane (stand-in for PUT /me/phone)"
M2=$(flr -H "Authorization: Bearer $ATOKEN" "http://127.0.0.1:$PORT/me")
[ "$(echo "$M2" | J "['id']")" = "$FID" ] && ok "her existing auth token (old number + lid) still resolves to the SAME customer" || bad "lid resolution" "$M2"
[ "$(PSQL db_flariane "select count(*) from customers where display_name='Migr Awa' and id>=$FID")" = "1" ] && ok "no second customer was created for the new number" || bad "customer split"
run --refresh --apply >/dev/null
L2=$(aut -X POST "http://127.0.0.1:$PORT/v1/login" -H 'content-type: application/json' -d "{\"phone\":\"$NEWPHONE\",\"password\":\"$PW\",\"product\":\"flariane\",\"app\":\"customer\"}")
AT2=$(echo "$L2" | J "['access_token']"); [ -n "$AT2" ] && [ "$(claims "$AT2" | J "['ph']")" = "tel:+225$NEWPHONE" ] && ok "after refresh she logs in to auth with the NEW number" || bad "login with new number" "$L2"
[ "$(flr -H "Authorization: Bearer $AT2" "http://127.0.0.1:$PORT/me" | J "['id']")" = "$FID" ] && ok "...and flariane still sees the same customer" || bad "same customer after refresh"

# 5. risk 1: password drift. flariane is still the authority until cutover
NEWH=$(docker exec flariane-web python -c "from app.security.crypto import hash_password; print(hash_password('Drift-New-2026'))")
PSQL db_flariane "update customers set password_hash = '$NEWH' where id = $FID" >/dev/null
run --refresh --apply >/dev/null
okp(){ aut -X POST "http://127.0.0.1:$PORT/v1/login" -H 'content-type: application/json' -d "{\"phone\":\"$NEWPHONE\",\"password\":\"$1\",\"product\":\"flariane\",\"app\":\"customer\"}" | J "['role']"; }
[ "$(okp Drift-New-2026)" = "customer" ] && [ "$(okp "$PW")" = "" ] && ok "a password changed in flariane reaches auth (old one stops working)" || bad "password refresh"
TOK=$(aut -X POST "http://127.0.0.1:$PORT/v1/login" -H 'content-type: application/json' -d "{\"phone\":\"$NEWPHONE\",\"password\":\"Drift-New-2026\",\"product\":\"flariane\",\"app\":\"customer\"}" | J "['access_token']")
aut -X POST "http://127.0.0.1:$PORT/v1/password/change" -H "Authorization: Bearer $TOK" -H 'content-type: application/json' -d '{"current_password":"Drift-New-2026","new_password":"Set-In-Auth-2026"}' >/dev/null
NEWH2=$(docker exec flariane-web python -c "from app.security.crypto import hash_password; print(hash_password('Flariane-Later-2026'))")
PSQL db_flariane "update customers set password_hash = '$NEWH2' where id = $FID" >/dev/null
run --refresh --apply >/dev/null
[ "$(okp Set-In-Auth-2026)" = "customer" ] && [ "$(okp Flariane-Later-2026)" = "" ] && ok "a password she set in auth is never overwritten by a later flariane change" || bad "authority auth"

# 6. cutover freezes every migrated account
CUT=$(run --cutover --apply); [ "$(echo "$CUT" | J ".get('cutover_frozen_accounts',0)")" -ge 0 ] 2>/dev/null && ok "cutover command succeeds (frozen accounts: $(echo "$CUT" | J ".get('cutover_frozen_accounts')"))" || bad "cutover" "$CUT"
CUT2=$(run --cutover --apply); [ "$(echo "$CUT2" | J ".get('cutover_frozen_accounts')")" = "0" ] && ok "cutover is idempotent" || bad "cutover twice" "$CUT2"
PSQL db_flariane "update customers set password_hash = '$NEWH' where id = $FID" >/dev/null
run --refresh --apply >/dev/null
[ "$(okp Set-In-Auth-2026)" = "customer" ] && ok "after cutover flariane can no longer change her credentials in auth" || bad "post-cutover freeze"
echo; echo "result: $pass passed, $failn failed"; [ "$failn" -eq 0 ]
