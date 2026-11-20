#!/usr/bin/env bash
# LOCAL FULL-CHAIN RUN: real auth + real fidelia-BE (Postgres, alembic, SSO on, number-change and erase hooks wired both ways) + the real
# import of fidelia's users, driven over HTTP. Needs infra + auth running. Touches only throwaway things: a database `fidelia_chain`, a
# container `fidelia-chain`, and (restored at the end) the hook setting of auth/.env.platform.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FID="${FIDELIA_BACKEND_DIR:-$ROOT/../fidelia/fidelia-BE/backend-api}"
cd "$ROOT/infra"; set -a; source .env; source secrets/e2e_auth.env; set +a
PORT="${HTTP_PORT:-8080}"
ENVF="$ROOT/auth/.env.platform"
SVC=$(grep '^AUTH_SERVICE_KEYS_JSON=' "$ENVF" | grep -o '"fidelia": *"[^"]*"' | sed 's/.*: *"\(.*\)"/\1/')
HOOK=$(python3 -c "import secrets;print(secrets.token_urlsafe(32))")
STATE=$(mktemp -d); chmod 777 "$STATE"
PSQL(){ docker compose exec -T -e PGPASSWORD="$POSTGRES_PASSWORD" postgres psql -U postgres -d "$1" -qAtc "$2"; }
fail=0
cleanup(){
  docker rm -f fidelia-chain >/dev/null 2>&1
  [ -f "$ENVF.chainbak" ] && mv "$ENVF.chainbak" "$ENVF" && (cd "$ROOT/auth" && docker compose -f compose.platform.yaml up -d --force-recreate --wait >/dev/null 2>&1)
  PSQL postgres "DROP DATABASE IF EXISTS fidelia_chain" >/dev/null 2>&1
  rm -rf "$STATE"
}
trap cleanup EXIT
wait_auth(){ for i in $(seq 1 40); do docker exec auth-web python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/healthz')" >/dev/null 2>&1 && return 0; sleep 2; done; return 1; }
CADDY_IP=$(docker inspect infra-caddy-1 --format '{{(index .NetworkSettings.Networks "infra_net").IPAddress}}')
run_py(){ docker run --rm --network infra_net --add-host "auth-api.${DOMAIN}:${CADDY_IP}" -e DOMAIN -e E2E_ADMIN_PHONE -e E2E_ADMIN_PASSWORD -e E2E_ADMIN_TOTP_SECRET \
  -v "$ROOT/tests/e2e_chain.py:/chain.py:ro" -v "$STATE:/state" python:3.12-slim sh -c "pip install -q --no-cache-dir httpx pyotp 2>&1 | grep -v -e notice -e WARNING; python /chain.py $1"; }

echo "== setup"
# the auth service learns where fidelia's hook lives (restored at the end)
cp "$ENVF" "$ENVF.chainbak"
grep -v '^AUTH_PRODUCT_HOOKS_JSON=' "$ENVF.chainbak" > "$ENVF"
echo "AUTH_PRODUCT_HOOKS_JSON={\"fidelia\": {\"url\": \"http://fidelia-chain:8000/api/internal/phone-changed\", \"key\": \"$HOOK\"}}" >> "$ENVF"
(cd "$ROOT/auth" && docker compose -f compose.platform.yaml up -d --force-recreate --wait >/dev/null 2>&1) && wait_auth && echo "  auth restarted with the hook configured" || { echo "  auth did not come back"; exit 1; }
docker exec auth-web alembic upgrade head >/dev/null 2>&1
PSQL postgres "DROP DATABASE IF EXISTS fidelia_chain" >/dev/null; PSQL postgres "CREATE DATABASE fidelia_chain" >/dev/null
docker rm -f fidelia-chain >/dev/null 2>&1
docker run -d --name fidelia-chain --network infra_net -v "$FID:/src:ro" -v "$ROOT/kit:/kit:ro" \
  -e FIDELIA_SECRET_KEY=chain-only-jwt-secret -e DATABASE_URL="postgresql+asyncpg://postgres:${POSTGRES_PASSWORD}@postgres:5432/fidelia_chain" -e FIDELIA_SEED_SAMPLE=1 \
  -e MOBILE_MONEY_SECRETS=dev-secret -e MOBILE_MONEY_PROVIDER=fake -e CELERY_BROKER_URL=memory:// -e CELERY_RESULT_BACKEND=cache+memory:// -e PUBLIC_READ_RATE_LIMIT=100000/minute \
  -e FIDELIA_ENCRYPTION_KEY=chain-only-encryption-key -e FIDELIA_AUTH_HOOK_KEY="$HOOK" \
  -e FIDELIA_SSO_ENABLED=1 -e FIDELIA_SSO_ISSUER="http://auth-api.${DOMAIN}:${PORT}" -e FIDELIA_SSO_JWKS_URL=http://auth-web:8000/.well-known/jwks.json \
  -e FIDELIA_SSO_AUTH_URL=http://auth-web:8000 -e FIDELIA_SSO_SERVICE_KEY="$SVC" -e PYTHONDONTWRITEBYTECODE=1 \
  python:3.11-slim-bookworm sh -c 'cp -r /src /work && cp -r /kit /kitwork && cd /work && for i in 1 2 3 4; do pip install -q --no-cache-dir -r requirements.txt -c requirements.lock "/kitwork[sso]" >/tmp/pip.log 2>&1 && break; sleep 3; done; alembic upgrade head >/tmp/alembic.log 2>&1; exec uvicorn app.main:app --host 0.0.0.0 --port 8000' >/dev/null
echo -n "  waiting for fidelia (alembic upgrade head on Postgres, then the app)"
up=0; for i in $(seq 1 120); do docker exec fidelia-chain python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/health')" >/dev/null 2>&1 && { up=1; break; }; echo -n .; sleep 4; done; echo
[ "$up" = 1 ] || { echo "  fidelia did not start"; docker logs fidelia-chain 2>&1 | tail -15; exit 1; }
echo "  fidelia up; its tables: $(PSQL fidelia_chain "select count(*) from information_schema.tables where table_schema='public'") (incl. auth_erase_outbox: $(PSQL fidelia_chain "select count(*) from information_schema.tables where table_name='auth_erase_outbox'"))"
echo "  users in fidelia: $(PSQL fidelia_chain "select string_agg(roles, ',') from users")"

# local-only hygiene: forget earlier runs' imports of fidelia ids 1..4 and the seeded phones, so this run imports them afresh
docker exec -i auth-web python - <<'PY'
import asyncio
from sqlalchemy import delete
from app.db import SessionLocal
from app.models import LegacyIdentity, User
from app.services import accounts

async def main():
    async with SessionLocal() as db:
        await db.execute(delete(LegacyIdentity).where(LegacyIdentity.product == "fidelia"))
        for phone in ("+2250700000002", "+2250700000003", "+2250700000004", "+2250700000009"):
            u = await accounts.user_by_phone(db, phone)
            if u is not None:
                await db.delete(u)
        await db.commit()

asyncio.run(main())
PY
echo "== import fidelia's users into auth (real code path, read-only source)"
docker exec -e FIDELIA_DATABASE_URL="postgresql://postgres:${POSTGRES_PASSWORD}@postgres:5432/fidelia_chain" auth-web python -m app.cli import-fidelia --apply 2>&1 | python3 -c "import sys,json;d=json.load(sys.stdin);print('  created',d['created'],'skipped',d.get('skipped_already_imported'),'errors',len(d['errors']),'admins needing a code',d['admins_needing_totp_setup'])"

echo "== phase 1: sign-in, relay, step-up, number change, frozen accounts, deletion (auth up)"
run_py p1 || fail=1
echo "== phase 2: auth goes DOWN, a customer deletes her fidelia account"
docker stop auth-web >/dev/null
run_py p2 || fail=1
pend=$(PSQL fidelia_chain "select count(*) from auth_erase_outbox where status='pending'")
[ "$pend" = "1" ] && echo "  PASS  the call to auth was queued (1 pending), the number sealed: $(PSQL fidelia_chain "select phone_sealed not like '%+225%' from auth_erase_outbox limit 1")" || { echo "  FAIL  outbox pending = $pend"; fail=1; }
docker start auth-web >/dev/null; wait_auth && echo "  auth is back" || { echo "  auth did not come back"; fail=1; }
out=$(docker exec -w /work fidelia-chain python -c "import asyncio,datetime;from app.services import auth_erase;print(asyncio.run(auth_erase.process_outbox(datetime.datetime.utcnow()+datetime.timedelta(minutes=5))))")
echo "  outbox run: $out"
[ "$(PSQL fidelia_chain "select count(*) from auth_erase_outbox where status='done' and phone_sealed=''")" = "1" ] && echo "  PASS  the retry told auth and the number was blanked" || { echo "  FAIL  the outbox row is not done"; fail=1; }
echo "== phase 3"
run_py p3 || fail=1
echo; [ "$fail" = 0 ] && echo "FULL CHAIN: all green" || echo "FULL CHAIN: FAILURES"
exit $fail
