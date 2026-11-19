#!/usr/bin/env bash
# Lock the Redis default user behind the admin password (per-product users are added by new-product.sh).
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; source .env; set +a
docker compose exec -T redis redis-cli --no-auth-warning -a "$REDIS_ADMIN_PASSWORD" \
  ACL SETUSER default on ">$REDIS_ADMIN_PASSWORD" '~*' '&*' '+@all' >/dev/null
docker compose exec -T redis redis-cli --no-auth-warning -a "$REDIS_ADMIN_PASSWORD" ACL SAVE >/dev/null
echo "redis default user protected"
