#!/usr/bin/env bash
# Local age-encrypted backup (no S3). Writes logs/s3_backup.log so health CLI sees backup.
# Optional: if ~/.config/grok-farm/backup.env exists with S3 keys, also run s3_backup.sh.
set -euo pipefail

FARM_DIR="${FARM_DIR:-$HOME/grok-farm}"
LOG_DIR="${FARM_DIR}/logs"
BACKUP_DIR="${FARM_DIR}/backups"
mkdir -p "$LOG_DIR" "$BACKUP_DIR"
LOG="$LOG_DIR/s3_backup.log"
exec >>"$LOG" 2>&1

echo "======== $(date -Is) local age backup start ========"

if [[ -f "$HOME/.config/grok-farm/backup.env" ]]; then
  echo "backup.env present — delegating to s3_backup.sh"
  exec bash "$FARM_DIR/scripts/s3_backup.sh"
fi

AGE_PUB=""
if [[ -f "$HOME/.config/grok-farm/age.pubkey" ]]; then
  AGE_PUB="$(tr -d '[:space:]' < "$HOME/.config/grok-farm/age.pubkey")"
fi
if [[ -z "$AGE_PUB" ]]; then
  echo "FATAL: no age.pubkey and no backup.env"
  exit 1
fi
if ! command -v age >/dev/null 2>&1; then
  echo "FATAL: age not installed"
  exit 1
fi

HOST="$(hostname -s 2>/dev/null || hostname)"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
WORK="$(mktemp -d /tmp/grok-local-backup.XXXXXX)"
cleanup() { rm -rf "$WORK"; }
trap cleanup EXIT

STAGE="$WORK/payload"
mkdir -p "$STAGE/credentials" "$STAGE/ssh" "$STAGE/app"

copy_if() {
  local src="$1" dest="$2"
  if [[ -e "$src" ]]; then
    mkdir -p "$(dirname "$dest")"
    cp -a "$src" "$dest"
    echo "  + $src"
  else
    echo "  - missing $src"
  fi
}

echo "Collecting from $FARM_DIR"
copy_if "$FARM_DIR/.env" "$STAGE/credentials/.env"
copy_if "$FARM_DIR/akun.db" "$STAGE/credentials/akun.db"
copy_if "$FARM_DIR/akun.db-wal" "$STAGE/credentials/akun.db-wal"
copy_if "$FARM_DIR/akun.db-shm" "$STAGE/credentials/akun.db-shm"
copy_if "$FARM_DIR/identities.json" "$STAGE/credentials/identities.json"
copy_if "$HOME/.ssh/id_ed25519" "$STAGE/ssh/id_ed25519"
copy_if "$HOME/.ssh/id_ed25519.pub" "$STAGE/ssh/id_ed25519.pub"
for f in brutal_farmer.sh workflow.py sync_proxies_from_9r.py import_db.py check_status.py \
         log_redact.py token_util.py alerts.py db_schema.py email_identity.py name_gen.py \
         mark_expired_tokens.py probe_tokens.py adaptive_concurrent.py reconcile_9router.py; do
  copy_if "$FARM_DIR/$f" "$STAGE/app/$f"
done
if [[ -d "$FARM_DIR/results" ]]; then
  tar czf "$STAGE/results.tgz" -C "$FARM_DIR" results 2>/dev/null || true
fi

{
  echo "host=$HOST"
  echo "stamp=$STAMP"
  echo "mode=local_age"
  date -Is
  if [[ -f "$STAGE/credentials/akun.db" ]]; then
    sqlite3 "$STAGE/credentials/akun.db" "SELECT status, COUNT(*) FROM accounts GROUP BY status;" 2>/dev/null || true
  fi
} > "$STAGE/MANIFEST.txt"

PLAIN="$WORK/grok-farm-${HOST}-${STAMP}.tgz"
tar czf "$PLAIN" -C "$STAGE" .
ENC="$BACKUP_DIR/grok-farm-${HOST}-${STAMP}.tgz.age"
age -r "$AGE_PUB" -o "$ENC" "$PLAIN"
chmod 600 "$ENC"
# keep last 14 local archives
find "$BACKUP_DIR" -name 'grok-farm-*.tgz.age' -type f -mtime +14 -delete 2>/dev/null || true
ln -sfn "$(basename "$ENC")" "$BACKUP_DIR/latest.tgz.age"
SIZE=$(du -h "$ENC" | awk '{print $1}')
echo "Upload OK (local) $ENC size=$SIZE"
echo "======== $(date -Is) backup done ========"
