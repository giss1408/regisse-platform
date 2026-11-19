#!/usr/bin/env bash
# Smoke test for the pilot product (SuiviProjet) running on the shared infra. Requires: infra up, suivi provisioned and up.
set -uo pipefail
cd "$(dirname "$0")/../infra"; set -a; source .env; set +a
pass=0; failn=0
ok(){ echo "  PASS  $1"; pass=$((pass+1)); }; bad(){ echo "  FAIL  $1"; failn=$((failn+1)); }
H="Host: suivi-api.${DOMAIN}"; U="http://127.0.0.1:${HTTP_PORT}"
code(){ curl -s -o /dev/null -w '%{http_code}' -H "$H" "$U$1"; }

[ "$(code /healthz)" = 200 ] && ok "/healthz via caddy -> 200" || bad "/healthz via caddy"
c=$(code /account/login/); { [ "$c" = 200 ] || [ "$c" = 302 ]; } && ok "login page reachable ($c)" || bad "login page returned $c"
c=$(curl -s -o /dev/null -w '%{http_code}' -H "Host: evil.example" "$U/account/login/"); [ "$c" = 404 ] && ok "foreign host rejected at caddy ($c)" || bad "foreign host got $c"
n=$(docker compose exec -T -e PGPASSWORD="$POSTGRES_PASSWORD" postgres psql -U postgres -d db_suivi -Atc "select count(*) from django_migrations"); [ "${n:-0}" -gt 10 ] && ok "migrations applied in db_suivi ($n)" || bad "migrations count '$n'"
u=$(docker exec suivi-web id -u); [ "$u" != 0 ] && ok "container runs non-root (uid $u)" || bad "container runs as root"
p=$(docker port suivi-web 2>/dev/null); [ -z "$p" ] && ok "suivi-web publishes no ports" || bad "ports published: $p"
l=$(docker inspect suivi-web --format '{{.HostConfig.Memory}}'); [ "${l:-0}" -gt 0 ] && ok "memory limit set ($l)" || bad "no memory limit"
s=$(docker exec suivi-web sh -c 'ls /app/staticfiles | head -1'); [ -n "$s" ] && ok "static files collected" || bad "no static files"
h=$(curl -sI -H "$H" -H "X-Forwarded-Proto: https" "$U/account/login/" | tr -d '\r' | grep -ci "content-security-policy"); [ "$h" -ge 1 ] && ok "security headers present (CSP)" || bad "CSP header missing"
echo; echo "result: $pass passed, $failn failed"; [ "$failn" -eq 0 ]
