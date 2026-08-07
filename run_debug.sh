#!/usr/bin/env bash
cd /home/grok/grok-farm
source .venv-v2/bin/activate
timeout 150 python3 debug_signup_dom.py > /tmp/dbg_full.log 2>&1
echo "EXIT:$?"
