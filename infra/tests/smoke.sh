#!/usr/bin/env bash
# End-to-end smoke test of the shared infra using throwaway products (names start with "smoke").
# Exit code 0 = all checks passed. Cleans up after itself.
set -uo pipefail
cd "$(dirname "$0")/.."
pass=0; failn=0
ok()   { echo "  PASS  $1"; pass=$((pass+1)); }
bad()  { echo "  FAIL  $1"; failn=$((failn+1)); }
check() { local d="$1"; shift; if "$@" >/dev/null 2>&1; then ok "$d"; else bad "$d"; fi; }
refuse() { local d="$1"; shift; if "$@" >/dev/null 2>&1; then bad "$d (should have been refused)"; else ok "$d"; fi; }

scripts/init.sh >/dev/null
set -a; source .env; set +a
PSQL() { docker compose exec -T -e PGPASSWORD="$POSTGRES_PASSWORD" postgres psql -v ON_ERROR_STOP=1 -U postgres "$@"; }

cleanup() {
  docker rm -f smokea-web >/dev/null 2>&1
  for n in smokea smokeb; do scripts/remove-product.sh $n --yes >/dev/null 2>&1; done
  rm -rf backups/db_smoke*
}
trap cleanup EXIT

echo "== infra up"
make -s up >/dev/null 2>&1
check "caddy healthy"    docker compose exec -T caddy wget -qO- http://127.0.0.1:2019/config/
check "postgres healthy" docker compose exec -T postgres pg_isready -U postgres
check "redis healthy"    docker compose exec -T redis redis-cli --no-auth-warning -a "$REDIS_ADMIN_PASSWORD" ping

echo "== no stray published ports"
published=$(docker ps --filter label=com.docker.compose.project=infra --format '{{.Label "com.docker.compose.service"}} {{.Ports}}' | grep -- '->' | awk '{print $1}' | sort -u | tr '\n' ' ')
[ "$published" = "caddy " ] && ok "only caddy publishes ports" || bad "published ports from: $published"

echo "== provision two products"
check "new-product smokea (postgis)" scripts/new-product.sh smokea --postgis --upstream smokea-web:80
check "new-product smokeb"           scripts/new-product.sh smokeb
refuse "duplicate product refused"   scripts/new-product.sh smokea
refuse "bad name refused"            scripts/new-product.sh Bad_Name
check "secrets file mode 600"        test "$(stat -f '%Lp' secrets/smokea.env 2>/dev/null || stat -c '%a' secrets/smokea.env)" = "600"

echo "== caddy routes to a product container"
docker rm -f smokea-web >/dev/null 2>&1
docker run -d --rm --name smokea-web --network infra_net traefik/whoami >/dev/null 2>&1
code=000
for i in $(seq 1 12); do
  code=$(curl -s -o /dev/null -w '%{http_code}' -H "Host: smokea-api.${DOMAIN}" "http://127.0.0.1:${HTTP_PORT}/")
  [ "$code" = "200" ] && break; sleep 1
done
[ "$code" = "200" ] && ok "smokea-api.${DOMAIN} -> 200" || bad "routing returned $code"
code=$(curl -s -o /dev/null -w '%{http_code}' -H "Host: unknown-api.${DOMAIN}" "http://127.0.0.1:${HTTP_PORT}/")
[ "$code" = "404" ] && ok "unknown host -> 404" || bad "unknown host returned $code"

echo "== postgres isolation"
. secrets/smokea.env; A_URL="$DATABASE_URL"; A_REDIS="$REDIS_URL"
. secrets/smokeb.env; B_URL="$DATABASE_URL"
PC() { docker run --rm --network infra_net postgres:17-alpine psql -v ON_ERROR_STOP=1 "$@"; }
check  "role_smokea can use db_smokea"          PC "$A_URL" -c "create table t(id int); insert into t values (1); select * from t"
refuse "role_smokea cannot connect to db_smokeb" PC "${A_URL%/*}/db_smokeb" -c "select 1"
refuse "role_smokeb cannot read db_smokea"      PC "${B_URL%/*}/db_smokea" -c "select 1"
refuse "role_smokea cannot create roles"        PC "$A_URL" -c "create role hacker"
check  "postgis available in db_smokea"          PC "$A_URL" -c "select postgis_version()"
refuse "postgis not forced into db_smokeb"       PC "$B_URL" -c "select postgis_version()"

echo "== redis isolation"
RC() { docker run --rm --network infra_net redis:7-alpine redis-cli --no-auth-warning "$@"; }
check  "product user can SET/GET"        sh -c "docker run --rm --network infra_net redis:7-alpine sh -c 'redis-cli -u \"$A_REDIS\" set k v && redis-cli -u \"$A_REDIS\" get k'"
check  "product user cannot FLUSHALL"    sh -c "docker run --rm --network infra_net redis:7-alpine redis-cli --no-auth-warning -u \"$A_REDIS\" flushall 2>&1 | grep -q NOPERM"
check  "product user cannot CONFIG"      sh -c "docker run --rm --network infra_net redis:7-alpine redis-cli --no-auth-warning -u \"$A_REDIS\" config get maxmemory 2>&1 | grep -q NOPERM"
refuse "anonymous default user refused"  sh -c "docker run --rm --network infra_net redis:7-alpine redis-cli -h redis ping | grep -q PONG"

echo "== backup and restore"
check "backup runs"        scripts/backup.sh
check "dump files exist"   sh -c 'ls backups/db_smokea/*.dump && ls backups/db_smokeb/*.dump'
check "restore test passes" scripts/restore-test.sh
check "restore leaves no scratch db" sh -c "! docker compose exec -T -e PGPASSWORD=$POSTGRES_PASSWORD postgres psql -U postgres -Atc \"select datname from pg_database\" | grep -q restore_test_"

echo "== persistence across restart"
docker compose restart postgres redis >/dev/null 2>&1
sleep 5
check "data survives restart" PC "$A_URL" -c "select count(*) from t having count(*)=1"
check "redis user survives restart" docker run --rm --network infra_net redis:7-alpine redis-cli -u "$A_REDIS" get k

echo
echo "result: $pass passed, $failn failed"
[ "$failn" -eq 0 ]
