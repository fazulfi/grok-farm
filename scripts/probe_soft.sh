#!/usr/bin/env bash
# Soft token probe timer — inventory meta only (never --mark-error).
set -euo pipefail

FARM_DIR="${FARM_DIR:-${GROK_FARM_DIR:-$HOME/grok-farm}}"
FARM_DIR="${FARM_DIR/#\~/$HOME}"
LOG_DIR="${FARM_DIR}/logs"
mkdir -p "$LOG_DIR"
LOG="${LOG_DIR}/probe_soft.log"
LIMIT="${GROK_PROBE_TIMER_LIMIT:-100}"

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
echo "======== ${TS} probe_soft start limit=${LIMIT} ========" >>"$LOG"
set +e
"$PY" probe_tokens.py --limit "$LIMIT" >>"$LOG" 2>&1
RC=$?
set -e
echo "======== $(date -Is 2>/dev/null || date) probe_soft end exit=${RC} ========" >>"$LOG"
exit "$RC"
