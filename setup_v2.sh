#!/usr/bin/env bash
# setup_v2.sh — one-shot prep for Grok Farm v2 (metode baru).
# Menginstall Chrome + deps farm_v2.py + verifikasi turnstilePatch.
# Wajib non-root (focus exe). Run:  bash setup_v2.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
echo "=============================================="
echo "  Grok Farm v2 — setup (metode baru)"
echo "  dir: $ROOT"
echo "=============================================="

if [[ "$(id -u)" -eq 0 ]]; then
  echo "ERROR: jangan jalankan setup_v2 sebagai root/sudo."
  echo "  Pakai user biasa. Exit."
  exit 1
fi

# ── 1. System deps (Chrome + GUI libs + xvfb) ───────────────────────────────
if command -v apt-get >/dev/null 2>&1; then
  echo "[1/5] apt packages (Chrome + GUI libs)..."
  sudo apt-get update -y >/dev/null 2>&1 || true
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
    wget curl ca-certificates python3 python3-venv python3-pip xvfb \
    fonts-liberation libasound2t64 libdbus-glib-1-2 libgtk-3-0 libx11-xcb1 \
    libxcomposite1 libxdamage1 libxrandr2 libgbm1 libpango-1.0-0 \
    libxkbcommon0 libnss3 libnspr4 2>/dev/null || \
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
    wget curl ca-certificates python3 python3-venv python3-pip xvfb \
    fonts-liberation libasound2 libdbus-glib-1-2 libgtk-3-0 libx11-xcb1 \
    libxcomposite1 libxdamage1 libxrandr2 libgbm1 libpango-1.0-0 \
    libxkbcommon0 libnss3 libnspr4
fi

# ── 2. Google Chrome stable ──────────────────────────────────────────────────
echo "[2/5] Google Chrome..."
if ! command -v google-chrome-stable >/dev/null 2>&1; then
  wget -q https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb -O /tmp/chrome.deb
  sudo apt-get install -y -f /tmp/chrome.deb >/dev/null 2>&1 || \
    sudo dpkg -i /tmp/chrome.deb || \
    sudo apt-get install -y -f -y
  rm -f /tmp/chrome.deb
fi
google-chrome-stable --version

# ── 3. Python venv + deps ────────────────────────────────────────────────────
echo "[3/5] Python venv (farm_v2)..."
if [[ ! -d .venv-v2 ]]; then
  python3 -m venv .venv-v2
fi
# shellcheck disable=SC1091
source .venv-v2/bin/activate
pip install -q -U pip wheel setuptools
pip install -q curl_cffi playwright python-dotenv

echo "[4/5] Playwright Chrome channel (memakai Google Chrome stable sys)..."
# channel='chrome' memakai binary Google Chrome di atas, bukan Chromium bundel.
python3 -c "import playwright, curl_cffi; print('  deps OK: playwright + curl_cffi')"

# ── 5. Verifikasi turnstilePatch ─────────────────────────────────────────────
echo "[5/5] Verifikasi turnstilePatch..."
if [[ -f turnstilePatch/manifest.json && -f turnstilePatch/script.js ]]; then
  echo "  turnstilePatch ada (fallback eksperimen; aktifkan dgn V2_TURNSTILE_PATCH=1)"
else
  echo "  WARN: turnstilePatch/ gak ketemu (fallback opsional — boleh dihapus)"
fi

echo
echo "=============================================="
echo "  Setup v2 selesai."
echo "  Next:"
echo "    1) nano .env   # isi blok V2_* (MAILLDEZ, password)"
echo "    2) python3 farm_v2.py --dry-run 1   # validasi"
echo "    3) xvfb-run -a python3 farm_v2.py 5  # farm 5 akun"
echo "=============================================="
