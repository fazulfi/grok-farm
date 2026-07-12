#!/usr/bin/env bash
# Restore .env + akun.db from a local backup tarball onto a farm VPS.
# Usage: ./scripts/restore_farm.sh magadirxwin@FARM_IP ./backups/grok-farm-backup-XXX.tgz
set -euo pipefail

TARGET="${1:-}"
TARBALL="${2:-}"

if [[ -z "$TARGET" || -z "$TARBALL" || ! -f "$TARBALL" ]]; then
  echo "Usage: $0 user@host backup.tgz"
  exit 1
fi

echo "==> Stopping farmer (if any)"
ssh "$TARGET" "sudo systemctl stop grok-farmer 2>/dev/null || true"

echo "==> Uploading & extracting $TARBALL"
scp "$TARBALL" "$TARGET:/tmp/grok-farm-restore.tgz"
ssh "$TARGET" bash -s <<'EOF'
set -euo pipefail
cd $HOME
tar xzf /tmp/grok-farm-restore.tgz
chmod 600 grok-farm/.env 2>/dev/null || true
chmod 600 grok-farm/akun.db 2>/dev/null || true
rm -f /tmp/grok-farm-restore.tgz
echo "Restored under $HOME/grok-farm"
EOF

echo "Next: ssh $TARGET 'cd ~/grok-farm && python3 workflow.py && sudo systemctl start grok-farmer'"