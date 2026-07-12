#!/usr/bin/env bash
# Install inject/list helpers on the 9router gateway host.
# Usage: ./scripts/install_9router_helpers.sh root@GATEWAY -p 39999
set -euo pipefail

TARGET="${1:-}"
shift || true
SSH_OPTS=("$@")

if [[ -z "$TARGET" ]]; then
  echo "Usage: $0 user@gateway [ssh opts...]"
  exit 1
fi

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
scp "${SSH_OPTS[@]}" \
  "$ROOT/ops/list_proxies.py" \
  "$ROOT/ops/grok_cli_bulk_inject.py" \
  "$TARGET:/tmp/"

ssh "${SSH_OPTS[@]}" "$TARGET" bash -s <<'EOF'
set -euo pipefail
sudo mv /tmp/list_proxies.py /root/list_proxies.py
sudo mv /tmp/grok_cli_bulk_inject.py /root/grok_cli_bulk_inject.py
sudo chmod 755 /root/list_proxies.py /root/grok_cli_bulk_inject.py
python3 /root/list_proxies.py | head -3
echo "OK helpers installed"
EOF