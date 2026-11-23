#!/usr/bin/env bash
# Product network isolation: every isolated product has its own Docker network `net_<product>`; its containers join ONLY that one.
# The shared services (caddy, postgres, redis, the auth service, the monitoring agent) are attached to every product network, so
# a product reaches the database, auth and the proxy, but never another product's containers.
# `docker network connect` does not survive a container being recreated, so this is idempotent and runs after `make up` and new-product.sh.
#   SHARED_CONTAINERS="a b"  override the list (tests)      NET_FILTER='^net_'  which networks to serve (tests)
set -euo pipefail
cd "$(dirname "$0")/.."
shared="${SHARED_CONTAINERS:-}"
if [ -z "$shared" ]; then
  for svc in caddy postgres redis; do
    id=$(docker compose ps -q "$svc" 2>/dev/null || true)
    [ -n "$id" ] && shared="$shared $(docker inspect -f '{{.Name}}' "$id" | tr -d /)"
  done
  for n in auth-web monitoring-alloy-1; do docker inspect "$n" >/dev/null 2>&1 && shared="$shared $n"; done
fi
for net in $(docker network ls --format '{{.Name}}' --filter "name=${NET_FILTER:-^net_}"); do
  for c in $shared; do
    if ! docker inspect -f '{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}' "$c" | tr ' ' '\n' | grep -qx "$net"; then
      docker network connect "$net" "$c" && echo "attached $c to $net"
    fi
  done
done
