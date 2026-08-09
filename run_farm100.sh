#!/usr/bin/env bash
# run_farm100.sh — farm 100 akun (sequential, auto-inject per akun) + log progress
cd /home/grok/grok-farm
source .venv-v2/bin/activate
export PYTHONUNBUFFERED=1
echo "START $(date)" >> /tmp/farm100.log
xvfb-run -a python3 farm_v2.py 100 >> /tmp/farm100.log 2>&1
echo "DONE $(date) exit=$?" >> /tmp/farm100.log
