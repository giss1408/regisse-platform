#!/usr/bin/env bash
# Network isolation check that does NOT touch the running stack: stand-in containers on throwaway networks net_isoa / net_isob.
set -uo pipefail
cd "$(dirname "$0")/.."
pass=0; failn=0
ok()  { echo "  PASS  $1"; pass=$((pass+1)); }
bad() { echo "  FAIL  $1"; failn=$((failn+1)); }
cleanup() { docker rm -f isoshared isoa-web isob-web >/dev/null 2>&1; docker network rm net_isoa net_isob >/dev/null 2>&1; }
trap cleanup EXIT; cleanup
docker network create net_isoa >/dev/null && docker network create net_isob >/dev/null
docker run -d --name isoshared traefik/whoami >/dev/null           # stands for postgres / caddy / auth
for p in a b; do docker run -d --name iso${p}-web --network net_iso${p} traefik/whoami >/dev/null; done   # real listeners on :80: an unreachable target must mean "isolated", not "nothing listening"
SHARED_CONTAINERS="isoshared" NET_FILTER='^net_iso' scripts/connect-nets.sh >/dev/null
SHARED_CONTAINERS="isoshared" NET_FILTER='^net_iso' scripts/connect-nets.sh | grep -q attached && bad "second run is not idempotent" || ok "connect-nets is idempotent"
# a curl client inside the SOURCE container's network namespace (the whoami image has no shell or curl)
reach() { docker run --rm --network "container:$1" curlimages/curl -s -m 3 -o /dev/null "http://$2:80/"; }
reach isoa-web isoshared && ok "product A reaches the shared service" || bad "A cannot reach shared"
reach isob-web isoshared && ok "product B reaches the shared service" || bad "B cannot reach shared"
reach isoa-web isob-web && bad "A reached B (not isolated)" || ok "product A cannot reach product B"
reach isob-web isoa-web && bad "B reached A (not isolated)" || ok "product B cannot reach product A"
# negative control: the check must be able to fail. Putting B on A's network makes A reachable from B.
docker network connect net_isoa isob-web
reach isob-web isoa-web && ok "control: a shared network DOES let them talk (the isolation check can detect a leak)" || bad "control failed: the check cannot detect a leak"
echo; echo "result: $pass passed, $failn failed"; [ "$failn" -eq 0 ]
