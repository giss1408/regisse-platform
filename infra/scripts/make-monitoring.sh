#!/usr/bin/env bash
# Create the read-only monitoring identities (idempotent) and write their passwords into monitoring/.env.
#   Postgres: role `monitor` with pg_monitor only (can read statistics, cannot read any product table)
#   Redis:    ACL user `monitor` limited to INFO/PING/CLIENT/SLOWLOG/LATENCY/CONFIG GET style read commands
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; source .env; set +a
[ -f monitoring/.env ] || { cp monitoring/.env.example monitoring/.env; chmod 600 monitoring/.env; }
setval() { # key value: only fills an empty value, never overwrites
  if grep -qE "^$1=.+" monitoring/.env; then return; fi
  if grep -q "^$1=" monitoring/.env; then sed -i.bak "s|^$1=.*|$1=$2|" monitoring/.env && rm -f monitoring/.env.bak; else echo "$1=$2" >> monitoring/.env; fi
  echo "generated $1"
}
setval MONITOR_DB_PASSWORD "$(openssl rand -hex 24)"
setval MONITOR_REDIS_PASSWORD "$(openssl rand -hex 24)"
set -a; source monitoring/.env; set +a
PSQL() { docker compose exec -T -e PGPASSWORD="$POSTGRES_PASSWORD" postgres psql -v ON_ERROR_STOP=1 -U postgres "$@"; }
PSQL -c "DO \$\$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='monitor') THEN CREATE ROLE monitor LOGIN; END IF; END \$\$;" >/dev/null
PSQL -c "ALTER ROLE monitor PASSWORD '$MONITOR_DB_PASSWORD' NOSUPERUSER NOCREATEDB NOCREATEROLE CONNECTION LIMIT 5; GRANT pg_monitor TO monitor;" >/dev/null
PSQL -c "REVOKE CONNECT ON DATABASE postgres FROM PUBLIC; GRANT CONNECT ON DATABASE postgres TO monitor;" >/dev/null
RCLI() { docker compose exec -T redis redis-cli --no-auth-warning -a "$REDIS_ADMIN_PASSWORD" "$@"; }
RCLI ACL SETUSER monitor on ">$MONITOR_REDIS_PASSWORD" '~*' '-@all' '+info' '+ping' '+client|list' '+slowlog|get' '+slowlog|len' '+latency|latest' '+config|get' '+dbsize' '+scan' '+type' '+ttl' '+memory|usage' >/dev/null
RCLI ACL SAVE >/dev/null
echo "monitor identities ready; fill the GRAFANA_* values in monitoring/.env, then: make monitoring-up"
