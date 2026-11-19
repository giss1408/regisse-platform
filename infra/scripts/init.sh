#!/usr/bin/env bash
# Create .env from .env.example with generated secrets. Idempotent: never overwrites existing values.
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] || cp .env.example .env
chmod 600 .env
fill() { # key
  if ! grep -qE "^$1=.+" .env; then
    val=$(openssl rand -hex 24)
    if grep -q "^$1=" .env; then sed -i.bak "s|^$1=.*|$1=$val|" .env && rm -f .env.bak; else echo "$1=$val" >> .env; fi
    echo "generated $1"
  fi
}
fill POSTGRES_PASSWORD
fill REDIS_ADMIN_PASSWORD
echo ".env ready"
