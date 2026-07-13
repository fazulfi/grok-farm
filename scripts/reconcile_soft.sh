#!/usr/bin/env bash
# Soft reconcile timer — dry report only (no --mark-error / --write-notes by default).
set -euo pipefail

FARM_DIR="${FARM_DIR:-${GROK_FARM_DIR:-$HOME/grok-farm}}"
FARM_DIR="${FARM_DIR/#\~/$HOME}"
LOG_DIR="${FARM_DIR}/logs"
mkdir -p "$LOG_DIR"
LOG="${LOG_DIR}/reconcile_soft.log"

cd "$FARM_DIR"
if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

if [[ -x .venv/bin/python ]]; then
  PY=".venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PY="python3"
else
  PY="python"
fi

TS="$(date -Is 2>/dev/null || date)"
echo "======== ${TS} reconcile_soft start ========" >>"$LOG"
set +e
"$PY" reconcile_9router.py --json >>"$LOG" 2>&1
RC=$?
set -e
echo "======== $(date -Is 2>/dev/null || date) reconcile_soft end exit=${RC} ========" >>"$LOG"
exit "$RC"
