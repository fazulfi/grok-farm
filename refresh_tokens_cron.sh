#!/usr/bin/env bash
# refresh_tokens_cron.sh — cron wrapper: refresh token akun grok-farm tiap 5 jam
# Log: ~/grok-farm/logs/refresh.log (append)
cd /home/grok/grok-farm
source .venv-v2/bin/activate
mkdir -p logs
echo "=== REFRESH $(date -u +%Y-%m-%dT%H:%M:%SZ) ===" >> logs/refresh.log
timeout 580 python3 refresh_9router.py --v2 v2_sso.txt >> logs/refresh.log 2>&1
echo "exit=$?" >> logs/refresh.log
