#!/bin/bash
# Brutal Farmer v5 — proxy from 9router proxyPools; auto import+inject after each batch
ROOT=$(cd "$(dirname "$0")" && pwd)
cd "$ROOT" || exit 1
ACCOUNTS_PER_BATCH=20
CONCURRENT=1
BATCH_DELAY=10
# Mid-batch drain: while farm.py runs, periodically import+inject accounts.txt (seconds; 0=off)
MID_DRAIN_INTERVAL="${GROK_MID_DRAIN_INTERVAL:-120}"
trap 'echo "[FARMER] Stopped by signal"; exit 0' SIGINT SIGTERM

# GROK_PROXY_FILE diambil dari .env (jangan force usa_proxies)

# Logs must be writable by farmer user (root-owned workflow.log silently breaks auto inject)
ensure_logs() {
  for f in workflow.log farm_brutal.log; do
    if [ -e "$f" ] && [ ! -w "$f" ]; then
      # Cannot chown without sudo — fall back to user-owned sidecar
      echo "[FARMER] WARN: $f not writable by $(whoami); using ${f%.log}_user.log"
    fi
  done
  # Prefer writable path for inject log
  if [ -w workflow.log ] 2>/dev/null || [ ! -e workflow.log ]; then
    WORKFLOW_LOG=workflow.log
    : >>"$WORKFLOW_LOG" 2>/dev/null || WORKFLOW_LOG=workflow_user.log
  else
    WORKFLOW_LOG=workflow_user.log
  fi
  : >>"$WORKFLOW_LOG" 2>/dev/null || WORKFLOW_LOG=/tmp/grok-workflow-$(id -u).log
  touch "$WORKFLOW_LOG" 2>/dev/null || true
  export WORKFLOW_LOG
}

run_import_workflow() {
  local tag="${1:-post-batch}"
  echo "[FARMER] import+workflow ($tag) log=$WORKFLOW_LOG"
  if ! "$PY" ${ROOT}/import_db.py >>"$WORKFLOW_LOG" 2>&1; then
    echo "[FARMER] WARN: import_db failed ($tag) — see $WORKFLOW_LOG"
  fi
  if ! "$PY" ${ROOT}/workflow.py >>"$WORKFLOW_LOG" 2>&1; then
    echo "[FARMER] WARN: workflow failed ($tag) — see $WORKFLOW_LOG"
  fi
  # Surface last summary line into farm_brutal.log (stdout)
  tail -n 5 "$WORKFLOW_LOG" 2>/dev/null | grep -E 'Imported|SUMMARY|Pending|Finished|ERROR|Error' || true
}

ensure_logs

echo "[FARMER] ========================================"
echo "[FARMER] Brutal Farmer v5 — 9router proxyPools"
echo "[FARMER] Started at $(date) user=$(whoami) workflow_log=$WORKFLOW_LOG"
echo "[FARMER] ========================================"

# Load .env early (sebelum sync proxy) so GROK_SKIP_PROXY_SYNC & GROK_* kebaca
set -a
# shellcheck disable=SC1091
[ -f .env ] && . ./.env
set +a

while true; do
    echo "[FARMER] === Batch started at $(date) ==="
    ensure_logs

    # 1) Sync proxy list from 9router proxyPools (source of truth)
    if [ "${GROK_SKIP_PROXY_SYNC:-0}" != "1" ]; then
        echo "[FARMER] Syncing proxies from 9router proxyPools..."
        if ! python3 ${ROOT}/sync_proxies_from_9r.py; then
            echo "[FARMER] WARN: proxy sync failed — skip farm this round, retry in ${BATCH_DELAY}s"
            sleep "$BATCH_DELAY"
            continue
        fi
    else
        echo "[FARMER] GROK_SKIP_PROXY_SYNC=1 — pakai proxies.txt existing"
    fi


    set +a
    PY="${FARM_PY:-.venv/bin/python}"
    [[ -x "$PY" ]] || PY=python3
    ensure_logs

    # 1a) Empty local proxy file → skip farm (inject already fail-closed on empty proxyPools)
    PROXY_FILE="${GROK_PROXY_FILE:-./proxies.txt}"
    PROXY_FILE="${PROXY_FILE/#\~/$HOME}"
    PROXY_N=0
    if [ -f "$PROXY_FILE" ]; then
        PROXY_N=$(grep -cE '^(https?://|socks5?://|[^#[:space:]].*:)' "$PROXY_FILE" 2>/dev/null || echo 0)
    fi
    if [ "${PROXY_N:-0}" -eq 0 ] 2>/dev/null; then
        echo "[FARMER] WARN: proxy file empty ($PROXY_FILE) — skip farm this round, retry in ${BATCH_DELAY}s"
        sleep "$BATCH_DELAY"
        continue
    fi
    echo "[FARMER] proxy_file_lines=$PROXY_N"

    # 1b) Adaptive concurrent from domain_stats + proxy_stats (live-safe: next batch)
    if [ "${GROK_ADAPTIVE_CONCURRENT:-1}" != "0" ]; then
        CONCURRENT=$("$PY" adaptive_concurrent.py --print 2>/dev/null || echo "$CONCURRENT")
    fi
    echo "[FARMER] concurrent=$CONCURRENT (adaptive=${GROK_ADAPTIVE_CONCURRENT:-1})"

    # 2) Farm (optional mid-batch drain so 9router gets accounts before batch ends)
    MID_PID=""
    if [ "${MID_DRAIN_INTERVAL:-0}" -gt 0 ] 2>/dev/null; then
        (
            while true; do
                sleep "$MID_DRAIN_INTERVAL" || exit 0
                run_import_workflow "mid-batch"
            done
        ) &
        MID_PID=$!
        echo "[FARMER] mid-drain every ${MID_DRAIN_INTERVAL}s pid=$MID_PID"
    fi

    printf '%s\n' "$ACCOUNTS_PER_BATCH" "$CONCURRENT" "Y" | "$PY" farm.py
    EXIT_CODE=$?

    if [ -n "$MID_PID" ]; then
        kill "$MID_PID" 2>/dev/null || true
        wait "$MID_PID" 2>/dev/null || true
    fi

    # 3) Import + inject (must run as farmer user; log must be writable)
    echo "[FARMER] farm.py exit=$EXIT_CODE — import + workflow"
    run_import_workflow "post-batch"

    echo "[FARMER] === Batch finished at $(date) (farm_exit=$EXIT_CODE concurrent=$CONCURRENT) ==="
    if [ "$EXIT_CODE" -ne 0 ]; then
        echo "[FARMER] WARN: farm non-zero, continuing unlimited"
    fi
    echo "[FARMER] Waiting ${BATCH_DELAY}s..."
    sleep "$BATCH_DELAY"
done
