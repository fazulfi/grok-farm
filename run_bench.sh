#!/usr/bin/env bash
cd /home/grok/grok-farm
source .venv-v2/bin/activate
timeout 220 python3 benchmark_grok2.py > /tmp/bench.log 2>&1
echo "EXIT:$?" >> /tmp/bench.log
