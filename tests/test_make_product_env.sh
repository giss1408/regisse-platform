#!/usr/bin/env bash
# Tests infra/scripts/make-product-env.sh with temporary files (no docker, no real secrets).
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
mkdir -p "$T/secrets"
printf 'DATABASE_URL=postgresql://role_x:pw@postgres:5432/db_x\nREDIS_URL=redis://x:rpw@redis:6379/3\nPUBLIC_HOST=x-api.localhost\n' > "$T/secrets/x.env"
printf 'X_AUTH_HOOK_KEY=hookkey-hookkey-hookkey-hookkey-0123\n' > "$T/secrets/x.hooks.env"
printf 'DOMAIN=localhost\nSITE_SCHEME=http://\nHTTP_PORT=8080\n' > "$T/infra.env"
printf 'AUTH_X=1\nAUTH_SERVICE_KEYS_JSON={"x": "svc-key-for-x", "other": "no"}\n' > "$T/auth.env"
cat > "$T/defaults" <<'D'
# a comment
CELERY_BROKER_URL=${REDIS_URL}
X_SECRET=@random
X_OTHER_SECRET=@random
X_ISSUER=${URL_SCHEME}auth-api.${DOMAIN}${PORT_SUFFIX}
X_SERVICE_KEY=${SERVICE_KEY}
X_MODE=staging  # inline comment dropped
D
export SECRETS_DIR="$T/secrets" INFRA_ENV="$T/infra.env" AUTH_ENV="$T/auth.env"
pass=0; failn=0
ok(){ echo "  PASS  $1"; pass=$((pass+1)); }; bad(){ echo "  FAIL  $1"; failn=$((failn+1)); }
get(){ grep -E "^$2=" "$1" | tail -1 | cut -d= -f2-; }
M(){ bash "$ROOT/infra/scripts/make-product-env.sh" x "$T/out.env" --defaults "$T/defaults"; }

OUT=$(M)
[ "$(get "$T/out.env" DATABASE_URL)" = "postgresql://role_x:pw@postgres:5432/db_x" ] && [ "$(get "$T/out.env" PUBLIC_HOST)" = "x-api.localhost" ] && ok "database, redis and host come from the product's secrets file" || bad "base"
[ "$(get "$T/out.env" X_AUTH_HOOK_KEY)" = "hookkey-hookkey-hookkey-hookkey-0123" ] && ok "the hook key file is merged in" || bad "hooks"
[ "$(get "$T/out.env" CELERY_BROKER_URL)" = "redis://x:rpw@redis:6379/3" ] && ok "\${REDIS_URL} is expanded from the secrets" || bad "expansion"
[ "$(get "$T/out.env" X_ISSUER)" = "http://auth-api.localhost:8080" ] && ok "the issuer URL follows the infra scheme, domain and port" || bad "issuer: $(get "$T/out.env" X_ISSUER)"
[ "$(get "$T/out.env" X_SERVICE_KEY)" = "svc-key-for-x" ] && ok "the product's service key is read from auth's config (not another product's)" || bad "service key"
[ "$(get "$T/out.env" X_MODE)" = "staging" ] && ! grep -q "^#.*comment" <(tail -n +2 "$T/out.env") && ok "inline comments and comment lines are dropped" || bad "comments"
S1=$(get "$T/out.env" X_SECRET); S2=$(get "$T/out.env" X_OTHER_SECRET)
[ ${#S1} -eq 64 ] && [ ${#S2} -eq 64 ] && [ "$S1" != "$S2" ] && ok "@random makes distinct 64-hex secrets" || bad "random"
M >/dev/null
[ "$(get "$T/out.env" X_SECRET)" = "$S1" ] && [ "$(get "$T/out.env" X_OTHER_SECRET)" = "$S2" ] && ok "a second run keeps every generated secret" || bad "kept"
[ "$(stat -f %Lp "$T/out.env" 2>/dev/null || stat -c %a "$T/out.env")" = "600" ] && [ "$(stat -f %Lp "$T/secrets/x.generated.env" 2>/dev/null || stat -c %a "$T/secrets/x.generated.env")" = "600" ] && ok "the output and the generated-secrets file are mode 600" || bad "modes"
echo "$OUT" | grep -qF -e "$S1" -e "svc-key-for-x" && bad "a secret was printed" || ok "the script prints no secret"
echo "$OUT" | grep -q "EMPTY OR UNRESOLVED" && bad "unexpected unresolved: $OUT" || ok "everything resolved: the summary says so"
printf 'X_MISSING=${NOPE}\n' >> "$T/defaults"
M | grep -q "EMPTY OR UNRESOLVED: X_MISSING" && ok "an unresolved variable is reported, never silently empty" || bad "unresolved"
rm "$T/secrets/x.env"; MSG=$(bash "$ROOT/infra/scripts/make-product-env.sh" x "$T/o2" --defaults "$T/defaults" 2>&1 || true); echo "$MSG" | grep -q "run infra/scripts/new-product.sh x first" && ok "without new-product.sh's file it says what to do first" || bad "missing base"
echo; echo "result: $pass passed, $failn failed"; [ "$failn" -eq 0 ]
