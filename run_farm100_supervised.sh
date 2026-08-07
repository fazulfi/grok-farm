#!/usr/bin/env bash
# run_farm100_supervised.sh — farm 100 akun, auto-restart kalau crash, resume-safe
# Target: 100 akun BARU (skip email yang sudah ada di v2_sso.txt).
cd /home/grok/grok-farm
source .venv-v2/bin/activate
export PYTHONUNBUFFERED=1

BATCH=100
TARGET_COUNT=100
OUT_V2=v2_sso.txt

echo "SUPERVISED START $(date) target=$TARGET_COUNT" | tee -a /tmp/farm_sup.log

for round in 1 2 3 4 5; do
    # hitung berapa sudah
    DONE=$(grep -c "access_token" $OUT_V2 2>/dev/null || echo 0)
    REMAIN=$((TARGET_COUNT - DONE))
    echo "[round $round] done=$DONE remain=$REMAIN $(date)" | tee -a /tmp/farm_sup.log
    if [ "$REMAIN" -le 0 ]; then
        echo "TARGET CAPAI: $DONE akun" | tee -a /tmp/farm_sup.log
        break
    fi
    # jalankan farm batch (bisa jalan sebagian, dilanjut round berikutnya)
    xvfb-run -a python3 farm_v2.py "$BATCH" >> /tmp/farm_sup.log 2>&1
    RC=$?
    echo "[round $round] exit=$RC $(date)" | tee -a /tmp/farm_sup.log
    # kalau exit normal (0/1) dan selesai > 1 jam, berhenti
    if [ "$RC" -eq 0 ] && [ "$DONE" -ge "$REMAIN" ]; then
        break
    fi
    # anti-loop ketat: pause sebentar antar round
    sleep 20
done

echo "SUPERVISED DONE $(date)" | tee -a /tmp/farm_sup.log
