#!/usr/bin/env bash
# run_farm1.sh — jalankan farm 1 akun, log ke farm_out.log
cd /home/grok/grok-farm
source .venv-v2/bin/activate
rm -f /tmp/farm_out.log
noop=""
cd /home/grok/grok-farm
source .venv-v2/bin/activate
xvfb-run -a python3 farm_v2.py 1 >> /tmp/farm_out.log 2>&1
echo "EXIT:$?"
