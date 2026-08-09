#!/usr/bin/env bash
# sync_farm_backup.sh — tarik akun.db + .env dari FARM VPS ke local host (lindung dari VPS mati)
# Generic: ganti FARM_VPS + PORT + DEST_DIR sesuai setup kamu.
set -euo pipefail
FARM_VPS="${FARM_VPS:-FARM_VPS_HOST}"   # SSH host alias ke farm VPS
PORT="${PORT:-22}"                       # SSH port farm VPS (ganti ke 22041 kalau non-default)
DEST="${DEST_DIR:-./_backup_farm}"
mkdir -p "$DEST"
STAMP="$(date +%Y%m%d_%H%M%S)"

echo "== $(date) =="
# 1) ambil akun.db (inventory API keys) + .env (config)
scp -o ConnectTimeout=15 -o BatchMode=yes -o StrictHostKeyChecking=no -P "$PORT" "$FARM_VPS":~/grok-farm/akun.db "$DEST/akun.db" 2>&1 | head -2 || { echo "scp akun.db GAGAL"; exit 1; }
scp -o ConnectTimeout=15 -o BatchMode=yes -o StrictHostKeyChecking=no -P "$PORT" "$FARM_VPS":~/grok-farm/.env "$DEST/.env" 2>&1 | head -2 || echo "env gagal (skip)"

# 2) snapshot berstempel buat riwayat (keep last 20)
cp "$DEST/akun.db" "$DEST/akun.$STAMP.db" 2>/dev/null || true
ls -t "$DEST"/akun.*.db 2>/dev/null | tail -n +21 | xargs -r rm -f -- 2>/dev/null || true

# 3) verify
python3 -c "
import sqlite3
try:
    c=sqlite3.connect('$DEST/akun.db')
    n=c.execute('SELECT COUNT(*) FROM accounts').fetchone()[0]
    print(f'OK: akun.db {n} akun tersimpan di local host')
except Exception as e:
    print('verify GAGAL:', e)
    raise SystemExit(1)
"
