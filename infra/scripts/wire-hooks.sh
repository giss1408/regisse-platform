#!/usr/bin/env bash
# Wires the shared keys between the auth service and the products that move data when a person changes her number:
#   auth      AUTH_PRODUCT_HOOKS_JSON   (who to call, with which key)
#   fidelia   FIDELIA_AUTH_HOOK_KEY     (the same key, to accept the call)
#   flariane  FLR_AUTH_HOOK_KEY         (the same key, to accept the call)
# Idempotent: existing keys are kept. `--rotate fidelia|flariane|all` makes a new key and writes it on BOTH sides.
# `--restart` recreates the containers that read them. `--check` calls the live hooks (wrong key -> 403, right key -> ok) from the network.
# Keys live in infra/secrets/hooks.env (mode 600, never committed) and in the env files; this script never prints one (fingerprints only).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
KEYS_FILE="${KEYS_FILE:-$ROOT/infra/secrets/hooks.env}"
AUTH_ENV="${AUTH_ENV:-$ROOT/auth/.env.platform}"
FLARIANE_ENV="${FLARIANE_ENV:-$ROOT/../flariane/.env.platform}"
# fidelia is not deployed on the platform yet: its hook key lives with the other product secrets, outside any repository, in its own file (new-product.sh owns fidelia.env; make-product-env.sh merges both)
FIDELIA_ENV="${FIDELIA_ENV:-$ROOT/infra/secrets/fidelia.hooks.env}"
HOOK_URL_FIDELIA="${HOOK_URL_FIDELIA:-http://fidelia-web:8000/api/internal/phone-changed}"
HOOK_URL_FLARIANE="${HOOK_URL_FLARIANE:-http://flariane-web:8000/internal/phone-changed}"
ROTATE=""; RESTART=0; CHECK=0; PRODUCTS="fidelia,flariane"
while [ $# -gt 0 ]; do
  case "$1" in
    --rotate) ROTATE="${2:-}"; shift 2 ;;
    --restart) RESTART=1; shift ;;
    --check) CHECK=1; shift ;;
    --products) PRODUCTS="${2:-}"; shift 2 ;;  # only these products are listed in auth (default both)
    *) echo "usage: $0 [--rotate fidelia|flariane|all] [--products fidelia,flariane] [--restart] [--check]" >&2; exit 2 ;;
  esac
done
umask 077
mkdir -p "$(dirname "$KEYS_FILE")"; touch "$KEYS_FILE"; chmod 600 "$KEYS_FILE"

fp(){ printf '%s' "$1" | shasum -a 256 | cut -c1-8; }
getvar(){ { grep -E "^$2=" "$1" 2>/dev/null || true; } | tail -1 | cut -d= -f2-; }
setvar(){ # file var value: replace or append, keep other lines, keep mode 600
  python3 - "$1" "$2" "$3" <<'PY'
import os, sys
path, var, value = sys.argv[1:4]
lines = open(path).read().splitlines() if os.path.exists(path) else []
lines = [l for l in lines if not l.startswith(var + "=")] + [f"{var}={value}"]
open(path, "w").write("\n".join(lines) + "\n")
os.chmod(path, 0o600)
PY
}
newkey(){ python3 -c "import secrets;print(secrets.token_urlsafe(32))"; }

for p in fidelia flariane; do
  P=$(echo "$p" | tr a-z A-Z)
  cur="$(getvar "$KEYS_FILE" "HOOK_KEY_$P")"
  if [ -z "$cur" ] || [ "$ROTATE" = "$p" ] || [ "$ROTATE" = "all" ]; then
    setvar "$KEYS_FILE" "HOOK_KEY_$P" "$(newkey)"
    [ -n "$cur" ] && echo "rotated: $p (old $(fp "$cur"), new $(fp "$(getvar "$KEYS_FILE" "HOOK_KEY_$P")"))" || echo "generated: $p"
  fi
done
KF="$(getvar "$KEYS_FILE" HOOK_KEY_FIDELIA)"; KL="$(getvar "$KEYS_FILE" HOOK_KEY_FLARIANE)"

# auth: who to call
# A product listed here MUST be reachable: a number change for one of its people fails (503, nothing changes) while its hook is down.
# So list only the products that are deployed (`--products flariane` while fidelia is not).
JSON=$(python3 - "$PRODUCTS" "$HOOK_URL_FIDELIA" "$KF" "$HOOK_URL_FLARIANE" "$KL" <<'PY'
import json, sys
wanted = {p for p in sys.argv[1].split(",") if p}
u1, k1, u2, k2 = sys.argv[2:6]
all_ = {"fidelia": {"url": u1, "key": k1}, "flariane": {"url": u2, "key": k2}}
print(json.dumps({p: v for p, v in all_.items() if p in wanted}, separators=(",", ":")))
PY
)
setvar "$AUTH_ENV" AUTH_PRODUCT_HOOKS_JSON "$JSON"
echo "auth      : AUTH_PRODUCT_HOOKS_JSON set in $AUTH_ENV for: $PRODUCTS (fidelia $(fp "$KF"), flariane $(fp "$KL"))"
# the products: the same key, to accept the call
if [ -d "$(dirname "$FLARIANE_ENV")" ]; then setvar "$FLARIANE_ENV" FLR_AUTH_HOOK_KEY "$KL"; echo "flariane  : FLR_AUTH_HOOK_KEY set in $FLARIANE_ENV ($(fp "$KL"))"
else echo "flariane  : $(dirname "$FLARIANE_ENV") not found, nothing written"; fi
if [ -d "$(dirname "$FIDELIA_ENV")" ]; then setvar "$FIDELIA_ENV" FIDELIA_AUTH_HOOK_KEY "$KF"; echo "fidelia   : FIDELIA_AUTH_HOOK_KEY set in $FIDELIA_ENV ($(fp "$KF"))"
else echo "fidelia   : $(dirname "$FIDELIA_ENV") not found, nothing written"; fi

if [ "$RESTART" = 1 ]; then
  (cd "$ROOT/auth" && docker compose -f compose.platform.yaml up -d --force-recreate --wait >/dev/null) && echo "restarted : auth (web + scheduler)"
  [ -f "$ROOT/../flariane/compose.platform.yaml" ] && (cd "$ROOT/../flariane" && docker compose -f compose.platform.yaml up -d --force-recreate --wait >/dev/null) && echo "restarted : flariane"
  [ -f "$ROOT/../fidelia/fidelia-BE/backend-api/compose.platform.yaml" ] && (cd "$ROOT/../fidelia/fidelia-BE/backend-api" && docker compose -f compose.platform.yaml up -d --force-recreate --wait >/dev/null) && echo "restarted : fidelia" || true
fi

if [ "$CHECK" = 1 ]; then
  bad=0
  probe(){ # name url key -> prints "name: wrong key -> 403, right key -> 200" or fails
    docker run -i --rm --network infra_net -e URL="$2" -e KEY="$3" python:3.12-slim python - <<'PY'
import json, os, urllib.error, urllib.request
def call(key):
    req = urllib.request.Request(os.environ["URL"], data=json.dumps({"old_phone": "+2250700000771", "new_phone": "+2250700000772"}).encode(),
                                 headers={"content-type": "application/json", "X-Service-Key": key}, method="POST")
    try:
        return urllib.request.urlopen(req, timeout=10).status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return None
print(call("wrong-key-wrong-key-wrong-key"), call(os.environ["KEY"]))
PY
  }
  for p in fidelia flariane; do
    P=$(echo "$p" | tr a-z A-Z); url_var="HOOK_URL_$P"; key="$(getvar "$KEYS_FILE" "HOOK_KEY_$P")"
    host=$(echo "${!url_var}" | sed -E 's#http://([^:/]+).*#\1#')
    if ! docker ps --format '{{.Names}}' | grep -qx "$host"; then echo "check     : $p ($host) is not running here, skipped"; continue; fi
    res=$(probe "$p" "${!url_var}" "$key")
    if [ "$res" = "403 200" ]; then echo "check     : $p hook OK (wrong key refused, right key accepted)"; else echo "check     : $p hook FAILED (answers: $res)"; bad=1; fi
  done
  exit $bad
fi
