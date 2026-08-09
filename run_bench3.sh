#!/usr/bin/env bash
cd /home/grok/grok-farm
source .venv-v2/bin/activate
timeout 200 python3 benchmark_grok3.py > /tmp/bench3.log 2>&1
echo "EXIT:$?" >> /tmp/bench3.log
