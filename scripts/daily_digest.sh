#!/usr/bin/env bash
# Daily digest + proxy dashboard → Telegram (inventory only, zero ban risk).
# Called by grok-farm-digest.timer. Does not stop farmer.
set -euo pipefail

FARM_DIR="${FARM_DIR:-${GROK_FARM_DIR:-$HOME/grok-farm}}"
FARM_DIR="${FARM_DIR/#\~/$HOME}"
LOG_DIR="${FARM_DIR}/logs"
mkdir -p "$LOG_DIR"
LOG="${LOG_DIR}/daily_digest.log"

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
echo "======== ${TS} daily_digest start ========" >>"$LOG"

set +e
"$PY" daily_digest.py "$@" >>"$LOG" 2>&1
RC=$?
set -e

echo "exit_code=${RC}" >>"$LOG"
echo "======== $(date -Is 2>/dev/null || date) daily_digest end ========" >>"$LOG"
exit "$RC"
