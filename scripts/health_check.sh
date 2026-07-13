#!/usr/bin/env bash
# Inventory-only health check for Grok Farm.
# Runs check_status.py --json (no --probe / --mark-error), logs result,
# and fires GROK_ALERT_WEBHOOK / GROK_FARM_ALERT_WEBHOOK on exit 2 or failure.
# Zero ban risk: soft inventory only.
set -euo pipefail

FARM_DIR="${FARM_DIR:-${GROK_FARM_DIR:-$HOME/grok-farm}}"
FARM_DIR="${FARM_DIR/#\~/$HOME}"
LOG_DIR="${FARM_DIR}/logs"
mkdir -p "$LOG_DIR"
LOG="${LOG_DIR}/health_check.log"

cd "$FARM_DIR"

# Load farmer .env so identity pool + webhook match production
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
echo "======== ${TS} health_check start ========" >>"$LOG"

TMP="$(mktemp "${TMPDIR:-/tmp}/grok-health.XXXXXX")"
cleanup() { rm -f "$TMP"; }
trap cleanup EXIT

set +e
"$PY" check_status.py --json >"$TMP" 2>>"$LOG"
RC=$?
set -e

# Append JSON (or partial output) to log — no secrets expected in check_status JSON
{
  echo "exit_code=${RC}"
  cat "$TMP"
  echo "======== $(date -Is 2>/dev/null || date) health_check end ========"
} >>"$LOG"

# Fire webhook on unhealthy (2) or unexpected failure (!=0,2)
if [[ "$RC" -eq 2 ]] || [[ "$RC" -ne 0 ]]; then
  LEVEL="warning"
  TITLE="health check unhealthy"
  if [[ "$RC" -ne 0 && "$RC" -ne 2 ]]; then
    LEVEL="error"
    TITLE="health check failed"
  fi
  # Parse issues from JSON when possible; never print tokens
  BODY="$("$PY" - "$TMP" "$RC" <<'PY' 2>/dev/null || true
import json, sys
path, rc = sys.argv[1], sys.argv[2]
try:
    with open(path, encoding="utf-8", errors="replace") as f:
        data = json.load(f)
except Exception as e:
    print(f"exit={rc} parse_error={e}")
    raise SystemExit(0)
issues = data.get("issues") or []
farmer = data.get("farmer")
acc = data.get("accounts") or {}
parts = [
    f"exit={rc}",
    f"farmer={farmer}",
    f"issues={','.join(issues) if issues else '(none)'}",
    f"accounts total={acc.get('total')} injected={acc.get('injected')} farmed={acc.get('farmed')} error={acc.get('error')}",
]
print("; ".join(str(p) for p in parts))
PY
)"
  if [[ -z "${BODY// }" ]]; then
    BODY="exit=${RC} (no JSON body)"
  fi
  # alerts.send_alert no-ops if webhook unset
  "$PY" -c "
from alerts import send_alert
import sys
title = sys.argv[1]
body = sys.argv[2]
level = sys.argv[3]
ok = send_alert(title, body, level=level, extra={'exit_code': int(sys.argv[4]), 'source': 'health_check'})
print(f'[health_check] alert_sent={ok}', file=sys.stderr)
" "$TITLE" "$BODY" "$LEVEL" "$RC" >>"$LOG" 2>&1 || true
fi

exit "$RC"
