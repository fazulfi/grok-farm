#!/usr/bin/env bash
# Deploy / update Grok Farm on a remote VPS from this git repo.
# Usage:
#   ./scripts/deploy_farm_vps.sh magadirxwin@FARM_IP
#   ./scripts/deploy_farm_vps.sh magadirxwin@FARM_IP --branch main
set -euo pipefail

TARGET="${1:-}"
BRANCH="main"
shift || true
while [[ $# -gt 0 ]]; do
  case "$1" in
    --branch) BRANCH="$2"; shift 2 ;;
    *) echo "Unknown arg: $1"; exit 1 ;;
  esac
done

if [[ -z "$TARGET" ]]; then
  echo "Usage: $0 user@host [--branch main]"
  exit 1
fi

REPO_URL="${REPO_URL:-https://github.com/fazulfi/grok-farm.git}"
REMOTE_DIR="${REMOTE_DIR:-grok-farm}"

echo "==> Deploying $REPO_URL ($BRANCH) → $TARGET:~/$REMOTE_DIR"

ssh -o StrictHostKeyChecking=accept-new "$TARGET" bash -s <<EOF
set -euo pipefail
cd \$HOME
if [[ -d $REMOTE_DIR/.git ]]; then
  cd $REMOTE_DIR
  git fetch origin
  git checkout $BRANCH
  git pull --ff-only origin $BRANCH
else
  git clone --branch $BRANCH "$REPO_URL" $REMOTE_DIR
  cd $REMOTE_DIR
fi
chmod +x install.sh run.sh brutal_farmer.sh scripts/*.sh 2>/dev/null || true
chmod +x ops/*.py sync_proxies_from_9r.py workflow.py import_db.py check_status.py 2>/dev/null || true
if [[ ! -d .venv ]]; then
  ./install.sh
fi
if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "WARN: created .env from example — edit before farming"
fi
# refresh systemd unit if present
if [[ -f systemd/grok-farmer.service ]]; then
  UNIT=/tmp/grok-farmer.service.\$\$
  sed "s/magadirxwin/\$USER/g; s|/home/magadirxwin|\$HOME|g" systemd/grok-farmer.service > "\$UNIT"
  sudo cp "\$UNIT" /etc/systemd/system/grok-farmer.service
  rm -f "\$UNIT"
  sudo systemctl daemon-reload
  echo "systemd unit updated (not restarted — run: sudo systemctl restart grok-farmer)"
fi
echo "OK deploy complete on \$(hostname)"
EOF

echo "Done. Next on VPS: edit .env, then sudo systemctl enable --now grok-farmer"