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
# refresh systemd units if present (farmer not restarted; timers enabled)
install_unit() {
  local src="\$1" dest_name="\$2"
  if [[ ! -f "\$src" ]]; then
    return 0
  fi
  local tmp
  tmp=/tmp/\${dest_name}.\$\$
  if [[ "\$src" == *.timer ]]; then
    cp "\$src" "\$tmp"
  else
    sed "s/magadirxwin/\$USER/g; s|/home/magadirxwin|\$HOME|g" "\$src" > "\$tmp"
  fi
  sudo cp "\$tmp" "/etc/systemd/system/\${dest_name}"
  rm -f "\$tmp"
}

install_unit systemd/grok-farmer.service grok-farmer.service
install_unit systemd/grok-farm-backup.service grok-farm-backup.service
install_unit systemd/grok-farm-backup.timer grok-farm-backup.timer
install_unit systemd/grok-farm-health.service grok-farm-health.service
install_unit systemd/grok-farm-health.timer grok-farm-health.timer
install_unit systemd/grok-farm-probe.service grok-farm-probe.service
install_unit systemd/grok-farm-probe.timer grok-farm-probe.timer
install_unit systemd/grok-farm-reconcile.service grok-farm-reconcile.service
install_unit systemd/grok-farm-reconcile.timer grok-farm-reconcile.timer
install_unit systemd/grok-farm-mark-expired.service grok-farm-mark-expired.service
install_unit systemd/grok-farm-mark-expired.timer grok-farm-mark-expired.timer
install_unit systemd/grok-farm-digest.service grok-farm-digest.service
install_unit systemd/grok-farm-digest.timer grok-farm-digest.timer
sudo systemctl daemon-reload
for t in grok-farm-backup.timer grok-farm-health.timer grok-farm-probe.timer \
         grok-farm-reconcile.timer grok-farm-mark-expired.timer grok-farm-digest.timer; do
  if [[ -f "/etc/systemd/system/\$t" ]] || systemctl cat "\$t" &>/dev/null; then
    sudo systemctl enable --now "\$t" || true
  fi
done
echo "systemd units refreshed (farmer NOT restarted — run: sudo systemctl restart grok-farmer if needed)"
echo "OK deploy complete on \$(hostname)"
EOF

echo "Done. Next on VPS: edit .env if needed; farmer not auto-restarted. Health/backup/probe/reconcile/mark-expired/digest timers enabled when units present."