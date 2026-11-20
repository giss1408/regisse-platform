#!/usr/bin/env bash
# Tests infra/scripts/wire-hooks.sh against temporary env files (no docker, no real secrets).
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
mkdir -p "$T/flr" "$T/fid"
# the default fidelia env is outside every repository
bash -c 'grep -q "infra/secrets/fidelia.env" "$0"' "$ROOT/infra/scripts/wire-hooks.sh" && echo "  PASS  the default fidelia env file lives in infra/secrets, not in a repository" || echo "  FAIL  default fidelia env"; echo "OTHER=keep-me" > "$T/auth.env"; echo "FLR_X=1" > "$T/flr/.env.platform"
export KEYS_FILE="$T/hooks.env" AUTH_ENV="$T/auth.env" FLARIANE_ENV="$T/flr/.env.platform" FIDELIA_ENV="$T/fid/.env.platform"
pass=0; failn=0
ok(){ echo "  PASS  $1"; pass=$((pass+1)); }; bad(){ echo "  FAIL  $1"; failn=$((failn+1)); }
W(){ bash "$ROOT/infra/scripts/wire-hooks.sh" "$@"; }
get(){ grep -E "^$2=" "$1" | tail -1 | cut -d= -f2-; }
hookkey(){ python3 -c "import json,sys;print(json.loads(sys.argv[1])['$2']['key'])" "$(get "$AUTH_ENV" AUTH_PRODUCT_HOOKS_JSON)"; }

OUT=$(W)
KF=$(get "$KEYS_FILE" HOOK_KEY_FIDELIA); KL=$(get "$KEYS_FILE" HOOK_KEY_FLARIANE)
[ ${#KF} -ge 32 ] && [ ${#KL} -ge 32 ] && [ "$KF" != "$KL" ] && ok "two different keys of 32+ characters are generated" || bad "keys"
[ "$(hookkey "$AUTH_ENV" fidelia)" = "$KF" ] && [ "$(hookkey "$AUTH_ENV" flariane)" = "$KL" ] && ok "auth gets each product's key in AUTH_PRODUCT_HOOKS_JSON" || bad "auth json"
[ "$(get "$FLARIANE_ENV" FLR_AUTH_HOOK_KEY)" = "$KL" ] && [ "$(get "$FIDELIA_ENV" FIDELIA_AUTH_HOOK_KEY)" = "$KF" ] && ok "each product gets the SAME key it is called with" || bad "product keys"
python3 -c "import json,sys;d=json.loads(sys.argv[1]);assert d['fidelia']['url'].endswith('/api/internal/phone-changed') and d['flariane']['url'].endswith('/internal/phone-changed')" "$(get "$AUTH_ENV" AUTH_PRODUCT_HOOKS_JSON)" && ok "the hook URLs are the private-network ones" || bad "urls"
grep -q "OTHER=keep-me" "$AUTH_ENV" && grep -q "FLR_X=1" "$FLARIANE_ENV" && ok "other lines of the env files are kept" || bad "other lines"
[ "$(stat -f %Lp "$KEYS_FILE" 2>/dev/null || stat -c %a "$KEYS_FILE")" = "600" ] && [ "$(stat -f %Lp "$AUTH_ENV" 2>/dev/null || stat -c %a "$AUTH_ENV")" = "600" ] && ok "the files holding keys are mode 600" || bad "modes"
echo "$OUT" | grep -qF -e "$KF" -e "$KL" && bad "a key was printed" || ok "no key is ever printed (fingerprints only)"

OUT2=$(W)
[ "$(get "$KEYS_FILE" HOOK_KEY_FIDELIA)" = "$KF" ] && [ "$(get "$KEYS_FILE" HOOK_KEY_FLARIANE)" = "$KL" ] && [ "$(grep -c '^AUTH_PRODUCT_HOOKS_JSON=' "$AUTH_ENV")" = 1 ] && ok "a second run changes nothing (idempotent, one line)" || bad "idempotence"

W --rotate fidelia >/dev/null
NF=$(get "$KEYS_FILE" HOOK_KEY_FIDELIA)
[ "$NF" != "$KF" ] && [ "$(get "$KEYS_FILE" HOOK_KEY_FLARIANE)" = "$KL" ] && ok "--rotate fidelia changes only fidelia's key" || bad "rotate scope"
[ "$(hookkey "$AUTH_ENV" fidelia)" = "$NF" ] && [ "$(get "$FIDELIA_ENV" FIDELIA_AUTH_HOOK_KEY)" = "$NF" ] && [ "$(hookkey "$AUTH_ENV" flariane)" = "$KL" ] && ok "...on both sides at once (auth and fidelia agree, flariane untouched)" || bad "rotate sides"
W --rotate all >/dev/null
[ "$(get "$KEYS_FILE" HOOK_KEY_FLARIANE)" != "$KL" ] && [ "$(get "$FLARIANE_ENV" FLR_AUTH_HOOK_KEY)" = "$(hookkey "$AUTH_ENV" flariane)" ] && ok "--rotate all rotates both" || bad "rotate all"

W --products flariane >/dev/null
python3 -c "import json,sys;d=json.loads(sys.argv[1]);assert list(d)==['flariane']" "$(get "$AUTH_ENV" AUTH_PRODUCT_HOOKS_JSON)" && ok "--products flariane lists only flariane in auth (an undeployed product would block its people's number changes)" || bad "products filter"
W >/dev/null
python3 -c "import json,sys;d=json.loads(sys.argv[1]);assert sorted(d)==['fidelia','flariane']" "$(get "$AUTH_ENV" AUTH_PRODUCT_HOOKS_JSON)" && ok "the default lists both again" || bad "default products"

rm -rf "$T/fid"
OUT3=$(W)
echo "$OUT3" | grep -q "fidelia   : .* not found" && ok "a product without a checkout is reported, not invented" || bad "missing product"
echo; echo "result: $pass passed, $failn failed"; [ "$failn" -eq 0 ]
