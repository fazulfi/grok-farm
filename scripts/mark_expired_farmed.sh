#!/usr/bin/env bash
# Mark expired farmed JWTs as error (farmed only — never default-include injected).
set -euo pipefail

FARM_DIR="${FARM_DIR:-${GROK_FARM_DIR:-$HOME/grok-farm}}"
FARM_DIR="${FARM_DIR/#\~/$HOME}"
LOG_DIR="${FARM_DIR}/logs"
mkdir -p "$LOG_DIR"
LOG="${LOG_DIR}/mark_expired_farmed.log"

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
echo "======== ${TS} mark_expired_farmed start ========" >>"$LOG"
set +e
"$PY" mark_expired_tokens.py --json >>"$LOG" 2>&1
RC=$?
set -e
echo "======== $(date -Is 2>/dev/null || date) mark_expired_farmed end exit=${RC} ========" >>"$LOG"
exit "$RC"
