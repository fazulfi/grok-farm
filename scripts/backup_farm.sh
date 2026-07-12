#!/usr/bin/env bash
# Backup secrets + inventory from a farm VPS (excludes .venv, screenshots).
# Usage: ./scripts/backup_farm.sh magadirxwin@FARM_IP ./backups
set -euo pipefail

TARGET="${1:-}"
OUT_DIR="${2:-./backups}"
STAMP="$(date +%Y%m%d_%H%M%S)"

if [[ -z "$TARGET" ]]; then
  echo "Usage: $0 user@host [out_dir]"
  exit 1
fi

mkdir -p "$OUT_DIR"
ARCHIVE="$OUT_DIR/grok-farm-backup-$STAMP.tgz"

echo "==> Backing up $TARGET → $ARCHIVE"
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
  > "$ARCHIVE"

echo "OK $(du -h "$ARCHIVE" | awk '{print $1}')"
echo "Store encrypted offline. Contains credentials."