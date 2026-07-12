#!/bin/bash
# Brutal Farmer v4 — proxy always from 9router proxyPools
cd /home/magadirxwin/grok-farm || exit 1
ACCOUNTS_PER_BATCH=20
CONCURRENT=3
BATCH_DELAY=10
trap 'echo "[FARMER] Stopped by signal"; exit 0' SIGINT SIGTERM

# Ensure .env points to synced proxy file
if ! grep -q 'GROK_PROXY_FILE=.*usa_proxies.txt' .env 2>/dev/null; then
  sed -i 's|GROK_PROXY_FILE=.*|GROK_PROXY_FILE=~/grok-farm/usa_proxies.txt|' .env 2>/dev/null || true
fi

echo "[FARMER] ========================================"
echo "[FARMER] Brutal Farmer v4 — 9router proxyPools"
echo "[FARMER] Started at $(date)"
echo "[FARMER] ========================================"

while true; do
    echo "[FARMER] === Batch started at $(date) ==="

    # 1) Sync proxy list from 9router proxyPools (source of truth)
    echo "[FARMER] Syncing proxies from 9router proxyPools..."
    if ! python3 /home/magadirxwin/grok-farm/sync_proxies_from_9r.py; then
        echo "[FARMER] WARN: proxy sync failed — skip farm this round, retry in ${BATCH_DELAY}s"
        sleep "$BATCH_DELAY"
        continue
    fi

    # 2) Farm
    printf '%s\n' "$ACCOUNTS_PER_BATCH" "$CONCURRENT" "Y" | .venv/bin/python farm.py
    EXIT_CODE=$?

    # 3) Import + inject (inject also picks random proxy from 9router pools)
    echo "[FARMER] farm.py exit=$EXIT_CODE — import + workflow"
    python3 /home/magadirxwin/grok-farm/import_db.py >> workflow.log 2>&1 || true
    python3 /home/magadirxwin/grok-farm/workflow.py >> workflow.log 2>&1 || true

    echo "[FARMER] === Batch finished at $(date) (farm_exit=$EXIT_CODE) ==="
    if [ "$EXIT_CODE" -ne 0 ]; then
        echo "[FARMER] WARN: farm non-zero, continuing unlimited"
    fi
    echo "[FARMER] Waiting ${BATCH_DELAY}s..."
    sleep "$BATCH_DELAY"
done
