#!/usr/bin/env bash
# Create the TLS material of the shared Postgres (idempotent): a private CA and a server certificate for the names products use.
#   secrets/pg-tls/ca.crt     give to clients that verify the server (sslmode=verify-full)
#   secrets/pg-tls/server.crt / server.key   mounted into the postgres container (never committed: secrets/* is gitignored)
# Certificates are valid 2 years: rerun with --renew before expiry (the CA stays; clients keep trusting it), then `docker compose restart postgres`.
set -euo pipefail
cd "$(dirname "$0")/.."
dir=secrets/pg-tls; mkdir -p "$dir"; chmod 700 "$dir"
renew=0; [ "${1:-}" = "--renew" ] && renew=1
if [ ! -f "$dir/ca.key" ]; then
  openssl req -x509 -newkey rsa:4096 -nodes -keyout "$dir/ca.key" -out "$dir/ca.crt" -days 3650 -subj "/CN=regisse-platform-pg-ca" >/dev/null 2>&1
  chmod 600 "$dir/ca.key"; echo "created CA"
fi
if [ ! -f "$dir/server.crt" ] || [ "$renew" = 1 ]; then
  openssl req -newkey rsa:3072 -nodes -keyout "$dir/server.key" -out "$dir/server.csr" -subj "/CN=postgres" >/dev/null 2>&1
  printf 'subjectAltName=DNS:postgres,DNS:localhost,IP:127.0.0.1\nextendedKeyUsage=serverAuth\n' > "$dir/san.ext"
  openssl x509 -req -in "$dir/server.csr" -CA "$dir/ca.crt" -CAkey "$dir/ca.key" -CAcreateserial -out "$dir/server.crt" -days 730 -extfile "$dir/san.ext" >/dev/null 2>&1
  rm -f "$dir/server.csr" "$dir/san.ext"; chmod 600 "$dir/server.key"; chmod 644 "$dir/server.crt" "$dir/ca.crt"
  echo "created server certificate (valid 730 days)"
fi
echo "postgres TLS ready in infra/$dir"
