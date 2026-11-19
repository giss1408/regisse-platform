#!/usr/bin/env bash
# Restore the newest dump of a database (default: every db_*) into a scratch DB and sanity-check it.
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; source .env; set +a
BACKUP_DIR="${BACKUP_DIR:-./backups}"
PG() { docker compose exec -T -e PGPASSWORD="$POSTGRES_PASSWORD" postgres "$@"; }
fail=0
for dir in "$BACKUP_DIR"/db_*/; do
  [ -d "$dir" ] || { echo "no backups found"; exit 1; }
  db=$(basename "$dir")
  f=$(ls -1t "$dir"* 2>/dev/null | head -1)
  [ -n "$f" ] || { echo "no dump for $db"; fail=1; continue; }
  case "$f" in *.age) echo "$f is encrypted: decrypt with your age identity first (AGE_IDENTITY)"; [ -n "${AGE_IDENTITY:-}" ] || { fail=1; continue; }
     age -d -i "$AGE_IDENTITY" -o "${f%.age}" "$f"; f="${f%.age}";; esac
  scratch="restore_test_${db}"
  PG psql -U postgres -qc "DROP DATABASE IF EXISTS $scratch" -c "CREATE DATABASE $scratch" >/dev/null
  if PG pg_restore -U postgres -d "$scratch" --no-owner --exit-on-error < "$f"; then
    tables=$(PG psql -U postgres -d "$scratch" -Atc "select count(*) from information_schema.tables where table_schema not in ('pg_catalog','information_schema')")
    echo "OK   $db restored from $(basename "$f"): $tables tables"
  else
    echo "FAIL $db restore from $(basename "$f")"; fail=1
  fi
  PG psql -U postgres -qc "DROP DATABASE IF EXISTS $scratch" >/dev/null
done
exit $fail
