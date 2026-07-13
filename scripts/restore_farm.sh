#!/usr/bin/env bash
# Restore .env + akun.db from a local backup tarball onto a farm VPS.
# Usage:
#   ./scripts/restore_farm.sh magadirxwin@FARM_IP ./backups/grok-farm-backup-XXX.tgz
#   ./scripts/restore_farm.sh magadirxwin@FARM_IP ./backups/xxx.tgz.age [age_identity_file]
set -euo pipefail

TARGET="${1:-}"
TARBALL="${2:-}"
AGE_IDENTITY="${3:-${AGE_IDENTITY:-$HOME/.config/grok-farm/age.identity}}"

if [[ -z "$TARGET" || -z "$TARBALL" || ! -f "$TARBALL" ]]; then
  echo "Usage: $0 user@host backup.tgz|.tgz.age [age_identity]"
  exit 1
fi

WORK="$(mktemp -d)"
cleanup() { rm -rf "$WORK"; }
trap cleanup EXIT

SRC="$TARBALL"
if [[ "$TARBALL" == *.age ]]; then
  if ! command -v age >/dev/null 2>&1; then
    echo "FATAL: age required to decrypt"
    exit 1
  fi
  if [[ ! -f "$AGE_IDENTITY" ]]; then
    echo "FATAL: age identity missing: $AGE_IDENTITY"
    exit 1
  fi
  SRC="$WORK/restore.tgz"
  age -d -i "$AGE_IDENTITY" -o "$SRC" "$TARBALL"
  echo "==> Decrypted age archive"
fi

echo "==> Stopping farmer (if any)"
ssh "$TARGET" "sudo systemctl stop grok-farmer 2>/dev/null || true"

echo "==> Uploading & extracting"
scp "$SRC" "$TARGET:/tmp/grok-farm-restore.tgz"
ssh "$TARGET" bash -s <<'EOF'
set -euo pipefail
cd $HOME
tar xzf /tmp/grok-farm-restore.tgz
# support both payload layout and flat grok-farm/
if [[ -d credentials ]]; then
  mkdir -p grok-farm
  cp -a credentials/.env grok-farm/.env 2>/dev/null || true
  cp -a credentials/akun.db grok-farm/akun.db 2>/dev/null || true
fi
chmod 600 grok-farm/.env 2>/dev/null || true
chmod 600 grok-farm/akun.db 2>/dev/null || true
rm -f /tmp/grok-farm-restore.tgz
echo "Restored under $HOME/grok-farm"
EOF

echo "Next: ssh $TARGET 'cd ~/grok-farm && python3 workflow.py && sudo systemctl start grok-farmer'"
