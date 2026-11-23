#!/usr/bin/env bash
# Dump every product database (custom format), optionally age-encrypt, optionally push to R2 via rclone.
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; source .env; set +a
BACKUP_DIR="${BACKUP_DIR:-./backups}"; keep="${BACKUP_KEEP_DAYS:-30}"
ts=$(date -u +%Y%m%dT%H%M%SZ)
dbs=$(docker compose exec -T -e PGPASSWORD="$POSTGRES_PASSWORD" postgres psql -U postgres -Atc "select datname from pg_database where datname like 'db\_%' escape '\\'")
[ -n "$dbs" ] || { echo "no product databases"; exit 0; }
for db in $dbs; do
  dir="$BACKUP_DIR/$db"; mkdir -p "$dir"
  out="$dir/$db-$ts.dump"
  docker compose exec -T -e PGPASSWORD="$POSTGRES_PASSWORD" postgres pg_dump -U postgres -Fc "$db" > "$out"
  [ -s "$out" ] || { echo "empty dump for $db"; rm -f "$out"; exit 1; }
  if [ -n "${AGE_RECIPIENT:-}" ]; then
    age -r "$AGE_RECIPIENT" -o "$out.age" "$out" && rm -f "$out" && out="$out.age"
  fi
  echo "backup $db -> $out ($(wc -c < "$out") bytes)"
  find "$dir" -type f -mtime +"$keep" -delete
done
if [ -n "${RCLONE_REMOTE:-}" ]; then
  rclone copy "$BACKUP_DIR" "$RCLONE_REMOTE" --immutable --no-traverse
  echo "pushed to $RCLONE_REMOTE"
else
  echo "RCLONE_REMOTE not set: backups are local only"
fi
# Dead-man's switch: a monitor (Better Stack / Healthchecks / Grafana synthetic) pages when this ping stops arriving, so a silently failing backup is noticed.
# Only reached when every dump above succeeded (set -e). Local-only backups ping /fail-less: the URL decides what that means.
if [ -n "${BACKUP_HEARTBEAT_URL:-}" ]; then curl -fsS -m 10 --retry 3 "$BACKUP_HEARTBEAT_URL" >/dev/null && echo "heartbeat sent"; fi
