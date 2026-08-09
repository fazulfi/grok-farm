#!/usr/bin/env bash
cd /home/grok/grok-farm
source .venv-v2/bin/activate
export R9_BASE=https://router.YOURDOMAIN.com
export R9_TOKEN="${R9_TOKEN}"
timeout 190 python3 inject_grok_cli_9router.py --sso /tmp/acc_inject.json --proxies ~/grok-farm/proxies.txt > /tmp/inject.log 2>&1
echo "EXIT:$?" >> /tmp/inject.log
