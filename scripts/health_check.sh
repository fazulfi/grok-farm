#!/usr/bin/env bash
# Inventory-only health check for Grok Farm.
# Runs check_status.py --json (no --probe / --mark-error), logs result,
# and fires GROK_ALERT_WEBHOOK only on HARD issues (exit 3) or unexpected failure.
# Exit 2 = soft inventory noise (log only, no webhook spam).
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

# Alert only on hard (3) or unexpected failure (not 0/2/3)
# exit 2 = soft issues only — log, no webhook
if [[ "$RC" -eq 3 ]] || { [[ "$RC" -ne 0 ]] && [[ "$RC" -ne 2 ]] && [[ "$RC" -ne 3 ]]; }; then
  LEVEL="warning"
  TITLE="health check hard issues"
  if [[ "$RC" -ne 0 && "$RC" -ne 2 && "$RC" -ne 3 ]]; then
    LEVEL="error"
    TITLE="health check failed"
  fi
  BODY="$("$PY" - "$TMP" "$RC" <<'PY' 2>/dev/null || true
import json, sys
path, rc = sys.argv[1], sys.argv[2]
try:
    with open(path, encoding="utf-8", errors="replace") as f:
        data = json.load(f)
except Exception as e:
    print(f"exit={rc} parse_error={e}")
    raise SystemExit(0)
hard = data.get("issues_hard") or []
soft = data.get("issues_soft") or []
issues = data.get("issues") or []
farmer = data.get("farmer")
acc = data.get("accounts") or {}
parts = [
    f"exit={rc}",
    f"farmer={farmer}",
    f"hard={','.join(hard) if hard else '(none)'}",
    f"soft={','.join(soft) if soft else '(none)'}",
    f"issues={','.join(issues) if issues else '(none)'}",
    f"accounts total={acc.get('total')} injected={acc.get('injected')} farmed={acc.get('farmed')} error={acc.get('error')}",
]
print("; ".join(str(p) for p in parts))
PY
)"
  if [[ -z "${BODY// }" ]]; then
    BODY="exit=${RC} (no JSON body)"
  fi
  "$PY" -c "
from alerts import send_alert
import sys, json
title = sys.argv[1]
body = sys.argv[2]
level = sys.argv[3]
rc = int(sys.argv[4])
path = sys.argv[5]
extra = {'exit_code': rc, 'source': 'health_check'}
try:
    with open(path, encoding='utf-8', errors='replace') as f:
        data = json.load(f)
    extra['issues'] = data.get('issues_hard') or data.get('issues') or []
except Exception:
    pass
ok = send_alert(title, body, level=level, extra=extra)
print(f'[health_check] alert_sent={ok}', file=sys.stderr)
" "$TITLE" "$BODY" "$LEVEL" "$RC" "$TMP" >>"$LOG" 2>&1 || true
elif [[ "$RC" -eq 2 ]]; then
  echo "[health_check] soft issues only (no webhook) exit=2" >>"$LOG"
fi

exit "$RC"
