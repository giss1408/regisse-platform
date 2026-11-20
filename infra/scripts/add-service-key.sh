#!/usr/bin/env bash
# Gives a product its service key in the auth service (AUTH_SERVICE_KEYS_JSON in auth/.env.platform). The key lets the product call auth's
# service endpoints (introspect, erase, staff...) and ONLY for its own product. Idempotent: an existing key is kept; `--rotate` replaces it
# (then re-run make-product-env.sh for the product and restart both sides). Never prints a key (fingerprint only).
# usage: add-service-key.sh <product> [--rotate]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
AUTH_ENV="${AUTH_ENV:-$ROOT/auth/.env.platform}"
name="${1:-}"; rotate=0; [ "${2:-}" = "--rotate" ] && rotate=1
[[ "$name" =~ ^[a-z][a-z0-9]{1,24}$ ]] || { echo "usage: $0 <product> [--rotate]" >&2; exit 2; }
[ -f "$AUTH_ENV" ] || { echo "$AUTH_ENV not found" >&2; exit 1; }
umask 077
python3 - "$AUTH_ENV" "$name" "$rotate" <<'PY'
import hashlib, json, os, secrets, sys
path, name, rotate = sys.argv[1], sys.argv[2], sys.argv[3] == "1"
lines = open(path).read().splitlines()
cur, rest = {}, []
for l in lines:
    if l.startswith("AUTH_SERVICE_KEYS_JSON="):
        cur = json.loads(l.split("=", 1)[1] or "{}")
    else:
        rest.append(l)
action = "kept"
if name not in cur or rotate:
    action = "rotated" if name in cur else "generated"
    cur[name] = secrets.token_urlsafe(32)
rest.append("AUTH_SERVICE_KEYS_JSON=" + json.dumps(cur, separators=(",", ":")))
open(path, "w").write("\n".join(rest) + "\n")
os.chmod(path, 0o600)
print(f"service key for {name}: {action} (fingerprint {hashlib.sha256(cur[name].encode()).hexdigest()[:8]}); products with a key: {', '.join(sorted(cur))}")
PY
