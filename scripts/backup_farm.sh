#!/usr/bin/env bash
# Backup secrets + inventory from a farm VPS (excludes .venv, screenshots).
# Usage: ./scripts/backup_farm.sh USER@FARM_IP ./backups [age1recipient]
# If AGE recipient given or AGE_RECIPIENT set, output is .tgz.age
set -euo pipefail

TARGET="${1:-}"
OUT_DIR="${2:-./backups}"
AGE_RECIPIENT="${3:-${AGE_RECIPIENT:-}}"
STAMP="$(date +%Y%m%d_%H%M%S)"

if [[ -z "$TARGET" ]]; then
  echo "Usage: $0 user@host [out_dir] [age1recipient]"
  exit 1
fi

mkdir -p "$OUT_DIR"
PLAIN="$OUT_DIR/grok-farm-backup-$STAMP.tgz"

echo "==> Backing up $TARGET → $PLAIN"
ssh -o StrictHostKeyChecking=accept-new "$TARGET" \
  "tar czf - -C \$HOME \
    --exclude=grok-farm/.venv \
    --exclude=grok-farm/screenshots \
    --exclude=grok-farm/__pycache__ \
    --exclude='grok-farm/*.log' \
    grok-farm/.env \
    grok-farm/akun.db \
    grok-farm/results \
    grok-farm/brutal_farmer.sh \
    2>/dev/null || tar czf - -C \$HOME grok-farm/.env grok-farm/akun.db 2>/dev/null" \
  > "$PLAIN"
chmod 600 "$PLAIN"

ARCHIVE="$PLAIN"
if [[ -n "$AGE_RECIPIENT" ]]; then
  if ! command -v age >/dev/null 2>&1; then
    echo "FATAL: age not installed locally"
    exit 1
  fi
  ENC="${PLAIN}.age"
  age -r "$AGE_RECIPIENT" -o "$ENC" "$PLAIN"
  chmod 600 "$ENC"
  rm -f "$PLAIN"
  ARCHIVE="$ENC"
  echo "Encrypted → $ARCHIVE"
else
  echo "WARNING: plaintext archive (pass age recipient as \$3 or AGE_RECIPIENT for enterprise)"
fi

echo "OK $(du -h "$ARCHIVE" | awk '{print $1}') $ARCHIVE"
echo "Contains credentials — store offline; decrypt: age -d -i identity.txt -o out.tgz archive.tgz.age"
