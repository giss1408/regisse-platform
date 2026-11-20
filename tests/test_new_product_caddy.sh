#!/usr/bin/env bash
# The Caddy snippet new-product.sh writes: optional body limit and /admin allowlist, validated by Caddy itself (docker, caddy:2.8).
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
pass=0; failn=0; ok(){ echo "  PASS  $1"; pass=$((pass+1)); }; bad(){ echo "  FAIL  $1"; failn=$((failn+1)); }
S(){ (cd "$ROOT/infra" && bash scripts/new-product.sh zzz --caddy-only "$@"); }
PLAIN=$(S); FULL=$(S --max-body 6MB --admin-ips "203.0.113.0/24 2001:db8::/32")
echo "$PLAIN" | grep -q "request_body" && bad "plain has a body limit" || ok "by default there is no body limit and no admin allowlist"
echo "$PLAIN" | grep -q "admin_blocked" && bad "plain has an allowlist" || ok "...the allowlist is optional (a lock-out risk without a fixed address)"
echo "$FULL" | grep -q "max_size 6MB" && echo "$FULL" | grep -q "not client_ip 203.0.113.0/24 2001:db8::/32" && ok "--max-body and --admin-ips appear in the snippet" || bad "flags"
echo "$FULL" | grep -q "path /admin/\*" && ! echo "$FULL" | grep -q "path /admin/\* /platform" && ok "the allowlist covers /admin/ only: the product's own dashboard is protected by sign-in and authenticator code" || bad "paths"
echo "$PLAIN" | grep -q "@private path /internal/\* /api/internal/\*" && ok "the private routes stay hidden" || bad "private"
# Caddy validates the whole thing, with the real Caddyfile layout (global block + import of the snippet)
mkdir -p "$T/conf.d" "$T/global.d"; printf '%s\n' "$FULL" > "$T/conf.d/zzz.caddy"
sed 's#/etc/caddy#/cfg#g' "$ROOT/infra/Caddyfile" > "$T/Caddyfile"
OUT=$(docker run --rm -e ACME_EMAIL=a@b.c -v "$T/Caddyfile:/cfg/Caddyfile:ro" -v "$T/conf.d:/cfg/conf.d:ro" -v "$T/global.d:/cfg/global.d:ro" caddy:2.8-alpine caddy validate --config /cfg/Caddyfile --adapter caddyfile 2>&1); echo "$OUT" | grep -q "Valid configuration" && ok "Caddy validates the layout with the optional flags (and an empty global.d)" || bad "caddy validate: $OUT"
cp "$ROOT/infra/global.d/cloudflare.caddy.example" "$T/global.d/cloudflare.caddy"
OUT=$(docker run --rm -e ACME_EMAIL=a@b.c -v "$T/Caddyfile:/cfg/Caddyfile:ro" -v "$T/conf.d:/cfg/conf.d:ro" -v "$T/global.d:/cfg/global.d:ro" caddy:2.8-alpine caddy validate --config /cfg/Caddyfile --adapter caddyfile 2>&1); echo "$OUT" | grep -q "Valid configuration" && ok "...and with Cloudflare's trusted ranges switched on" || bad "caddy validate cloudflare: $OUT"
echo; echo "result: $pass passed, $failn failed"; [ "$failn" -eq 0 ]
