#!/usr/bin/env bash
cd /home/grok/grok-farm
source .venv-v2/bin/activate
python3 test_otp_proxy.py > /tmp/otp_proxy_test.log 2>&1
echo "EXIT: $?"
