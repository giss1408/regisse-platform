#!/usr/bin/env bash
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
printf 'OTHER=keep\nAUTH_SERVICE_KEYS_JSON={"flariane":"flr-key-flr-key-flr-key-flr-key-flr-0123"}\n' > "$T/auth.env"; export AUTH_ENV="$T/auth.env"
pass=0; failn=0; ok(){ echo "  PASS  $1"; pass=$((pass+1)); }; bad(){ echo "  FAIL  $1"; failn=$((failn+1)); }
key(){ python3 -c "import json,sys;print(json.loads([l for l in open('$AUTH_ENV') if l.startswith('AUTH_SERVICE_KEYS_JSON=')][0].split('=',1)[1])['$1'])"; }
OUT=$(bash "$ROOT/infra/scripts/add-service-key.sh" supermarket)
K1=$(key supermarket)
[ ${#K1} -ge 32 ] && [ "$(key flariane)" = "flr-key-flr-key-flr-key-flr-key-flr-0123" ] && grep -q "OTHER=keep" "$AUTH_ENV" && ok "a key is added; the other product's key and other lines are kept" || bad "add"
bash "$ROOT/infra/scripts/add-service-key.sh" supermarket >/dev/null; [ "$(key supermarket)" = "$K1" ] && [ "$(grep -c '^AUTH_SERVICE_KEYS_JSON=' "$AUTH_ENV")" = 1 ] && ok "idempotent: the key is kept, one line" || bad "idempotent"
bash "$ROOT/infra/scripts/add-service-key.sh" supermarket --rotate >/dev/null; [ "$(key supermarket)" != "$K1" ] && [ "$(key flariane)" = "flr-key-flr-key-flr-key-flr-key-flr-0123" ] && ok "--rotate changes only that product's key" || bad "rotate"
echo "$OUT" | grep -qF "$K1" && bad "a key was printed" || ok "the key is never printed"
[ "$(stat -f %Lp "$AUTH_ENV" 2>/dev/null || stat -c %a "$AUTH_ENV")" = "600" ] && ok "mode 600" || bad "mode"
bash "$ROOT/infra/scripts/add-service-key.sh" "Bad Name" 2>/dev/null && bad "bad name accepted" || ok "a bad product name is refused"
echo; echo "result: $pass passed, $failn failed"; [ "$failn" -eq 0 ]
