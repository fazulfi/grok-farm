#!/usr/bin/env bash
# Automatic backup: credentials + results → age-encrypt → S3
# Requires: age, python3+boto3 (or /opt/aws-cli-venv), backup.env
# Encrypt: AGE_RECIPIENT (age1...) in backup.env OR ~/.config/grok-farm/age.pubkey
set -euo pipefail

CREDS="${HOME}/.config/grok-farm/backup.env"
LOG_DIR="${HOME}/grok-farm/logs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/s3_backup.log"
exec >>"$LOG" 2>&1

echo "======== $(date -Is) backup start ========"

if [[ ! -f "$CREDS" ]]; then
  echo "FATAL: missing $CREDS"
  exit 1
fi
# shellcheck source=/dev/null
source "$CREDS"

: "${AWS_ACCESS_KEY_ID:?}"
: "${AWS_SECRET_ACCESS_KEY:?}"
: "${S3_ENDPOINT:?}"
: "${S3_BUCKET:?}"
S3_PREFIX="${S3_PREFIX:-farm-vps}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"
FARM_DIR="${FARM_DIR:-$HOME/grok-farm}"
ENCRYPT="${BACKUP_ENCRYPT:-age}"  # age | none (none is insecure; for emergency only)
AGE_PUB="${AGE_RECIPIENT:-}"
if [[ -z "$AGE_PUB" && -f "${HOME}/.config/grok-farm/age.pubkey" ]]; then
  AGE_PUB="$(tr -d '[:space:]' < "${HOME}/.config/grok-farm/age.pubkey")"
fi

export AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY
export AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-us-east-1}"
export AWS_EC2_METADATA_DISABLED=true

HOST="$(hostname -s 2>/dev/null || hostname)"
# Optional override when hostname != fleet tag (e.g. S3_HOST_KEY=grok4)
HOST_KEY="${S3_HOST_KEY:-$HOST}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
DAY="$(date -u +%Y-%m-%d)"
WORK="$(mktemp -d /tmp/grok-farm-backup.XXXXXX)"
cleanup() { find "$WORK" -mindepth 1 -delete 2>/dev/null || true; rmdir "$WORK" 2>/dev/null || true; }
trap cleanup EXIT

# S3 object root: avoid farm-vps/grokN/grokN when S3_PREFIX already ends with host key.
# Accept either:
#   S3_PREFIX=farm-vps          → s3://bucket/farm-vps/<host>/...
#   S3_PREFIX=farm-vps/grok4    → s3://bucket/farm-vps/grok4/...  (no double host)
S3_PREFIX="${S3_PREFIX%/}"
if [[ -z "$S3_PREFIX" ]]; then
  S3_PREFIX="farm-vps"
fi
if [[ "$S3_PREFIX" == "$HOST_KEY" || "$S3_PREFIX" == */"$HOST_KEY" ]]; then
  HOST_ROOT="$S3_PREFIX"
else
  HOST_ROOT="${S3_PREFIX}/${HOST_KEY}"
fi
echo "S3 host root: ${HOST_ROOT} (prefix=${S3_PREFIX} host_key=${HOST_KEY})"

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
copy_if "$HOME/.config/grok-farm/backup.env" "$STAGE/credentials/backup.env"
copy_if "$HOME/.aws/credentials" "$STAGE/credentials/aws-credentials"
copy_if "$HOME/.aws/config" "$STAGE/credentials/aws-config"
copy_if "$HOME/.ssh/id_ed25519" "$STAGE/ssh/id_ed25519"
copy_if "$HOME/.ssh/id_ed25519.pub" "$STAGE/ssh/id_ed25519.pub"
copy_if "$HOME/.ssh/config" "$STAGE/ssh/config"
for f in brutal_farmer.sh workflow.py sync_proxies_from_9r.py import_db.py check_status.py \
         log_redact.py token_util.py alerts.py db_schema.py email_identity.py name_gen.py \
         mark_expired_tokens.py probe_tokens.py adaptive_concurrent.py reconcile_9router.py; do
  copy_if "$FARM_DIR/$f" "$STAGE/app/$f"
done
copy_if "$FARM_DIR/identities.json" "$STAGE/credentials/identities.json"
copy_if "$FARM_DIR/usa_proxies.txt" "$STAGE/app/usa_proxies.txt"
if [[ -d "$FARM_DIR/results" ]]; then
  echo "  + results/"
  tar czf "$STAGE/results.tgz" -C "$FARM_DIR" results
fi

{
  echo "host=$HOST"
  echo "stamp=$STAMP"
  echo "encrypt=$ENCRYPT"
  date -Is
  uname -a
  if [[ -f "$STAGE/credentials/akun.db" ]]; then
    sqlite3 "$STAGE/credentials/akun.db" "SELECT status, COUNT(*) FROM accounts GROUP BY status;" 2>/dev/null || true
  fi
} > "$STAGE/MANIFEST.txt"

PLAIN="$WORK/grok-farm-${HOST}-${STAMP}.tgz"
tar czf "$PLAIN" -C "$STAGE" .
chmod 600 "$PLAIN"
SIZE=$(du -h "$PLAIN" | awk '{print $1}')
echo "Plain archive size=$SIZE"

UPLOAD_FILE="$PLAIN"
EXT="tgz"
if [[ "$ENCRYPT" == "age" ]]; then
  if ! command -v age >/dev/null 2>&1; then
    echo "FATAL: age not installed (apt install age / go install)"
    exit 1
  fi
  if [[ -z "$AGE_PUB" ]]; then
    echo "FATAL: set AGE_RECIPIENT in backup.env or write ~/.config/grok-farm/age.pubkey"
    exit 1
  fi
  ENC="$PLAIN.age"
  age -r "$AGE_PUB" -o "$ENC" "$PLAIN"
  chmod 600 "$ENC"
  rm -f "$PLAIN"
  UPLOAD_FILE="$ENC"
  EXT="tgz.age"
  echo "Encrypted with age recipient ${AGE_PUB:0:20}..."
  SIZE=$(du -h "$UPLOAD_FILE" | awk '{print $1}')
  echo "Encrypted size=$SIZE"
elif [[ "$ENCRYPT" == "none" ]]; then
  echo "WARNING: BACKUP_ENCRYPT=none — uploading plaintext secrets (not enterprise)"
else
  echo "FATAL: unknown BACKUP_ENCRYPT=$ENCRYPT (use age|none)"
  exit 1
fi

KEY="s3://${S3_BUCKET}/${HOST_ROOT}/${DAY}/grok-farm-${HOST_KEY}-${STAMP}.${EXT}"
LATEST="s3://${S3_BUCKET}/${HOST_ROOT}/latest.${EXT}"

echo "Upload $KEY"
export S3_ENDPOINT S3_BUCKET AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_DEFAULT_REGION
PY_UPLOAD="${FARM_DIR}/scripts/s3_upload.py"
if [[ -x "$FARM_DIR/.venv/bin/python" ]]; then
  PYBIN="$FARM_DIR/.venv/bin/python"
elif [[ -x /opt/aws-cli-venv/bin/python ]]; then
  PYBIN=/opt/aws-cli-venv/bin/python
else
  PYBIN=python3
fi
$PYBIN "$PY_UPLOAD" "$UPLOAD_FILE" "$KEY" "$LATEST"
echo "$STAMP $SIZE $KEY encrypt=$ENCRYPT" > "$WORK/LATEST.txt"
$PYBIN "$PY_UPLOAD" "$WORK/LATEST.txt" "s3://${S3_BUCKET}/${HOST_ROOT}/LATEST.txt"
echo "Upload OK"

# Retention via boto3 (no aws CLI dependency)
# HOST_ROOT is the full prefix under the bucket (no double host segment).
export RETENTION_DAYS S3_ENDPOINT S3_BUCKET S3_PREFIX HOST_NAME="$HOST_KEY" S3_HOST_ROOT="$HOST_ROOT"
$PYBIN "$PY_UPLOAD" --retention --days "${RETENTION_DAYS}" || echo "retention warning: non-zero exit (backup already uploaded)"

echo "======== $(date -Is) backup done ========"
