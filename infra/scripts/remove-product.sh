#!/usr/bin/env bash
# DESTRUCTIVE: drops a product's database, role, Redis user, Caddy route and secrets. Requires --yes.
set -euo pipefail
cd "$(dirname "$0")/.."
name="${1:-}"; [ "${2:-}" = "--yes" ] || { echo "usage: remove-product.sh <name> --yes   (drops db_$name permanently)"; exit 2; }
[[ "$name" =~ ^[a-z][a-z0-9]{1,24}$ ]] || { echo "bad name"; exit 2; }
set -a; source .env; set +a
PSQL() { docker compose exec -T -e PGPASSWORD="$POSTGRES_PASSWORD" postgres psql -v ON_ERROR_STOP=1 -U postgres -d postgres "$@"; }
PSQL -c "DROP DATABASE IF EXISTS db_$name WITH (FORCE)" -c "DROP ROLE IF EXISTS role_$name" >/dev/null
docker compose exec -T redis redis-cli --no-auth-warning -a "$REDIS_ADMIN_PASSWORD" ACL DELUSER "$name" >/dev/null || true
docker compose exec -T redis redis-cli --no-auth-warning -a "$REDIS_ADMIN_PASSWORD" ACL SAVE >/dev/null || true
rm -f "conf.d/$name.caddy" "secrets/$name.env"
docker compose exec -T caddy caddy reload --config /etc/caddy/Caddyfile >/dev/null 2>&1 || true
echo "removed product '$name'"
