#!/usr/bin/env python3
"""farm_v2.py — Grok/xAI farmer, NEW method (parallel prototype, does not touch farm.py).

Mirror of the audit: docs/AUDIT-2026-08-07.md
Metode baru (vs farm.py v2.3.2):
  - Engine      : Playwright channel='chrome' (bukan Camoufox) + turnstilePatch extension
  - Email       : MAILLDEZ-compatible temp-mail API (bukan IMAP Gmail)
  - Token       : OAuth device-code (auth.x.ai, jalur resmi) — headless-friendly, tanpa callback server
  - Output      : sso_cookies (untuk inject 9Router) + email|password|access|refresh kompatibel

TIDAK menyentuh farm.py / akun.db / production. File terpisah. Run dry (tanpa bikin akun):
    python3 farm_v2.py --dry-run 5
Run asli (butuh .env + MAILLDEZ + Chrome):
    python3 farm_v2.py 5
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import tempfile
import time
from pathlib import Path

import curl_cffi.requests as creq  # may need: pip install curl_cffi
from playwright.sync_api import sync_playwright

# dotenv optional; fallback parse manual
_ROOT = Path(__file__).resolve().parent
try:
    from dotenv import load_dotenv
    load_dotenv(_ROOT / ".env", override=False)
except ImportError:
    pass

# ── Config (env, prefix V2_ biar gak bentrok farm.py) ─────────────────────────
def _env(key: str, default: str = "") -> str:
    return (os.environ.get(key) or default).strip()

TS_DIR = Path(_env("V2_TURNSTILE_PATCH_DIR", str(_ROOT / "turnstilePatch"))).resolve()
# KOREKSI 2026-08-07: turnstilePatch BUKAN solusi inti (Chromium #40280325 sudah di-fix Sept 2025,
# CL 6917162). Yang menentukan lolos Turnstile: channel='chrome' + headed/xvfb + residential IP.
# Extension ini hanya fallback/eksperimen: V2_TURNSTILE_PATCH=1 untuk mengaktifkan.
TS_PATCH_ENABLED = _env("V2_TURNSTILE_PATCH", "0").lower() in ("1", "true", "yes")
PASSWORD = _env("V2_PASSWORD", "P@s5w0rd-Grok-Farm-2026!")
MAILLDEZ = _env("V2_MAILLDEZ_URL", "")
MAILLDEZ_DOMAINS = _env("V2_MAILLDEZ_DOMAINS", "").replace(" ", "").split(",") or []
ROUTER9_URL = _env("V2_ROUTER9_URL", "")
ROUTER9_PASS = _env("V2_ROUTER9_PASS", "")
HEADLESS = _env("V2_HEADLESS", "false").lower() in ("1", "true", "yes")
PROXY = _env("V2_PROXY", "")  # optional single proxy server OR file path (proxies.txt) via V2_PROXY_FILE
PROXY_FILE = _env("V2_PROXY_FILE", "")
OUT = Path(_env("V2_OUT", str(_ROOT / "v2_sso.txt")))

# ── AUTO-INJECT per akun ke 9Router (V2_AUTO_INJECT=1) ─────────────────────────
AUTO_INJECT = _env("V2_AUTO_INJECT", "0").lower() in ("1", "true", "yes")
R9_BASE = _env("V2_R9_BASE", "http://127.0.0.1:20228")
R9_TOKEN = _env("V2_R9_TOKEN", "")

# ── VISION CAPTCHA (interactive Turnstile solver) via 9router vision model ─────
# farm.py lama pakai ini utk lolos Turnstile image-puzzle yang gak bisa auto-click.
CAPTCHA_MODEL = _env("GROK_CAPTCHA_MODEL", "cx/gpt-5.6-luna")
# API key 9router utk call vision (dari apiKeys table, e.g. hiyuki)
CAPTCHA_API_KEY = _env("GROK_CAPTCHA_API_KEY", "")
# Base API 9router (domain — pakai curl+UA utk hindari CF 1010)
CAPTCHA_API_URL = _env("GROK_CAPTCHA_API_URL", "https://router.markettabrak.biz.id/v1/chat/completions")

SIGNUP_URL = "https://accounts.x.ai/sign-up?redirect=grok-com"
XAI_CLIENT_ID = "b1a00492-073a-47ea-816f-4c329264a828"
XAI_DEVICE_CODE = "https://auth.x.ai/oauth2/device/code"
XAI_TOKEN = "https://auth.x.ai/oauth2/token"
# Intel 2026-08-07 (grup farmer): scope + referrer memengaruhi health token.
# Akun "thinking jalan" umumnya punya scope `... conversations:read conversations:write`
# (cocokkan ke grok-build latest — scope baru muncul) + referrer. Bisa di-override via env.
XAI_SCOPE = _env(
    "V2_OAUTH_SCOPE",
    "openid profile email offline_access grok-cli:access api:access "
    "conversations:read conversations:write",
)
# Nilai `referrer` pada auth request. Grok-build login pakai "grok-build".
XAI_REFERRER = _env("V2_OAUTH_REFERRER", "grok-build")
# Header version (mencocokkan grok CLI / build terbaru — sesuaikan kalau xAI rilis baru)
XAI_CLIENT_VERSION = _env("V2_CLIENT_VERSION", "0.1.0")

_domain_idx = 0

def _next_domain() -> str:
    global _domain_idx
    if not MAILLDEZ_DOMAINS:
        return ""
    d = MAILLDEZ_DOMAINS[_domain_idx % len(MAILLDEZ_DOMAINS)]
    _domain_idx += 1
    return d


# ── IMAP OTP (Gmail, CF Email Routing → inbox) ───────────────────────────────
import imaplib
from email import message_from_bytes

IMAP_USER = _env("GROK_IMAP_USER", "")
IMAP_PASS = _env("GROK_IMAP_PASS", "").replace(" ", "")
IMAP_HOST = _env("GROK_IMAP_HOST", "imap.gmail.com")
IMAP_PORT = int(_env("GROK_IMAP_PORT", "993") or "993")


def _imap_connect(proxy_url: str | None = None):
    """Buka koneksi IMAP SSL — direct ATAU lewat HTTP CONNECT proxy (WebShare).

    VPS idcloudhost SG blokir egress 993 langsung; via proxy CONNECT tunnel
    berhasil (tested 2026-08-07, 3/10 proxy OK). Return imaplib instance."""
    import socket
    import ssl

    if not proxy_url:
        return imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT, timeout=15)

    try:
        from urllib.parse import urlparse
        u = urlparse(proxy_url)
        phost, pport = u.hostname, (u.port or 3128)
        puser, ppass = u.username or "", u.password or ""
    except Exception:
        return imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT, timeout=15)

    import base64
    # 1) establish raw TCP to proxy
    sock = socket.create_connection((phost, int(pport)), timeout=15)
    try:
        # 2) HTTP CONNECT handshake
        auth = base64.b64encode(f"{puser}:{ppass}".encode()).decode() if puser else ""
        req = (
            f"CONNECT {IMAP_HOST}:{IMAP_PORT} HTTP/1.1\r\n"
            f"Host: {IMAP_HOST}:{IMAP_PORT}\r\n"
        )
        if auth:
            req += f"Proxy-Authorization: Basic {auth}\r\n"
        req += "Proxy-Connection: Keep-Alive\r\n\r\n"
        sock.sendall(req.encode())
        resp = b""
        while b"\r\n\r\n" not in resp:
            chunk = sock.recv(512)
            if not chunk:
                break
            resp += chunk
        if not (resp.startswith(b"HTTP/1.0 200") or resp.startswith(b"HTTP/1.1 200")):
            raise ConnectionError(f"proxy CONNECT failed: {resp.split(b'\\r\\n')[0][:80]}")
        # 3) wrap in TLS (SNI = imap host)
        ctx = ssl.create_default_context()
        tls = ctx.wrap_socket(sock, server_hostname=IMAP_HOST)
    except Exception:
        sock.close()
        raise

    # 4) subclass IMAP4 that uses the given connected+TLSed socket
    class _IMAP4Via(imaplib.IMAP4):
        def _create_socket(self, timeout):
            return tls

    mail = _IMAP4Via(IMAP_HOST, IMAP_PORT)
    return mail

def read_otp_from_imap(target_email: str, timeout: float = 120, proxy_url: str | None = None,
                       since_ts: float | None = None) -> str | None:
    """Poll Gmail IMAP utk xAI confirmation code addressed ke target_email (catch-all).
    Mirip farm.py read_otp_from_imap_sync tapi versi kompak utk farm_v2.
    proxy_url: http://user:pass@host:port — dipakai kalau egress 993 diblokir (via CONNECT).
    since_ts: hanya proses email yg masuk setelah timestamp ini (hindari OTP lama/re-used)."""
    if not IMAP_USER or not IMAP_PASS:
        return None
    target_lower = target_email.lower()
    target_local = target_lower.split("@")[0]
    start = time.time()
    import email.utils as _eu
    while time.time() - start < timeout:
        try:
            mail = _imap_connect(proxy_url)
            mail.login(IMAP_USER, IMAP_PASS)
            mail.select("INBOX")
            st, msgs = mail.search(None, '(FROM "x.ai")')
            mids = msgs[0].split() if msgs and msgs[0] else []
            if not mids:
                st, msgs = mail.search(None, '(SUBJECT "confirmation code")')
                mids = msgs[0].split() if msgs and msgs[0] else []
            for mid in reversed(mids[-60:]):
                st, data = mail.fetch(mid, "(RFC822)")
                if not data or not data[0]:
                    continue
                msg = message_from_bytes(data[0][1])
                subj = (msg.get("Subject", "") or "")
                # skip email yg masuk SEBELUM since_ts (re-used / stale OTP)
                if since_ts is not None:
                    try:
                        date_str = msg.get("Date", "") or ""
                        dt = _eu.parsedate_to_datetime(date_str)
                        if dt and dt.timestamp() < since_ts:
                            continue
                    except Exception:
                        pass
                # perbaiki: cek SUBJECT dulu utk confirmation code + recipient di header
                to_all = " ".join(filter(None, [
                    msg.get("To", ""), msg.get("Delivered-To", ""),
                    msg.get("X-Original-To", ""), msg.get("X-Forwarded-To", ""),
                    msg.get("Envelope-To", ""), msg.get("Received", ""),
                ])).lower()
                body = ""
                if msg.is_multipart():
                    for part in msg.walk():
                        if part.get_content_type() == "text/plain":
                            try:
                                body = part.get_payload(decode=True).decode("utf-8", "replace")
                            except Exception:
                                body = ""
                            if body:
                                break
                else:
                    try:
                        body = msg.get_payload(decode=True).decode("utf-8", "replace")
                    except Exception:
                        body = str(msg.get_payload() or "")
                # match recipient alias (catch-all): WAJIB target di To/Delivered-To ATAU
                # local-part di body. JANGAN match hanya karena subject confirmation code
                # (line longgar ini bikin baca OTP email akun LAIN → invalid).
                header_hit = (target_lower in to_all) or (len(target_local) >= 8 and target_local in to_all)
                body_hit = target_lower in body.lower() or (len(target_local) >= 8 and target_local in body.lower())
                if header_hit or body_hit:
                    for txt in (subj, body):
                        g = re.search(r"([A-Z0-9]{3}-[A-Z0-9]{3})", txt)
                        if g:
                            mail.logout()
                            return g.group(1).replace("-", "")
                        g = re.search(r"([A-Z0-9]{6})", txt)
                        if g:
                            mail.logout()
                            return g.group(1)
            mail.logout()
        except Exception:
            pass
        time.sleep(4)
    return None


# ── Temp-mail (MAILLDEZ-compatible JSON API) ──────────────────────────────────
class TempMail:
    """GET /api/session → sessionId; POST /api/inboxes → address; GET .../messages."""

    def __init__(self, base_url: str):
        self.base = base_url.rstrip("/")
        self.s = creq.Session()
        self.s.headers.update({"Accept": "application/json", "Content-Type": "application/json"})
        self.addr: str = ""

    def create(self) -> str:
        r = self.s.get(f"{self.base}/api/session", timeout=15).json()
        sid = r["sessionId"]
        self.s.headers["x-session-id"] = sid
        d = _next_domain()
        r = self.s.post(f"{self.base}/api/inboxes", json={"domain": d}, timeout=15).json()
        self.addr = r["address"]
        return self.addr

    def peek_code(self) -> str | None:
        for m in self.s.get(f"{self.base}/api/inboxes/{self.addr}/messages", timeout=15).json() or []:
            for txt in (m.get("subject", ""), m.get("body", "")):
                g = re.search(r"code:\s*([A-Z0-9]{3}-[A-Z0-9]{3})", txt, re.I)
                if g:
                    return g.group(1).replace("-", "")
                g = re.search(r"code:\s*([A-Z0-9]{6})", txt, re.I)
                if g:
                    return g.group(1)
        return None

    def wait_code(self, timeout: float = 120) -> str | None:
        t = time.time()
        while time.time() - t < timeout:
            c = self.peek_code()
            if c:
                return c
            time.sleep(4)
        return None


# ── xAI official OAuth device-code flow (validated live; Audit §4.3) ───────────
def xai_device_code() -> dict:
    r = creq.post(
        XAI_DEVICE_CODE,
        data={
            "client_id": XAI_CLIENT_ID,
            "scope": XAI_SCOPE,
            "referrer": XAI_REFERRER,
        },
        headers={
            "User-Agent": f"grok-build/{XAI_CLIENT_VERSION} (GrokFarm/v2 device)",
            "x-grok-client-version": XAI_CLIENT_VERSION,
        },
        timeout=20,
    )
    r.raise_for_status()
    return r.json()  # {device_code, user_code, verification_uri_complete, expires_in, interval}


def xai_poll_token(device_code: str, interval: int = 5, timeout: float = 180) -> dict:
    t = time.time()
    while time.time() - t < timeout:
        r = creq.post(
            XAI_TOKEN,
            data={
                "client_id": XAI_CLIENT_ID,
                "device_code": device_code,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            },
            headers={
                "User-Agent": f"grok-build/{XAI_CLIENT_VERSION} (GrokFarm/v2 device)",
                "x-grok-client-version": XAI_CLIENT_VERSION,
            },
            timeout=20,
        )
        data = r.json()
        if data.get("access_token"):
            return data
        if data.get("error") not in ("authorization_pending", "slow_down"):
            raise RuntimeError(f"device poll error: {data}")
        time.sleep(interval)
    raise TimeoutError("device-code approval timeout")


# ── 9Router integration (mirror dzDev37 /api/oauth/grok-cli/*) ────────────────
class NineRouter:
    def __init__(self, base: str, password: str):
        self.base = base.rstrip("/")
        self.passwd = password
        self.s = creq.Session()
        self.s.headers.update({"Accept": "application/json", "Content-Type": "application/json"})

    def login(self) -> bool:
        r = self.s.post(f"{self.base}/api/auth/login", json={"password": self.passwd}, timeout=15)
        return r.json().get("success", False)

    def device_code(self) -> dict:
        return self.s.get(f"{self.base}/api/oauth/grok-cli/device-code", timeout=10).json()

    def poll(self, device_code: str, code_verifier: str) -> dict:
        return self.s.post(
            f"{self.base}/api/oauth/grok-cli/poll",
            json={"deviceCode": device_code, "codeVerifier": code_verifier},
            timeout=10,
        ).json()


# ── Chrome + turnstilePatch (Audit §4.1) ───────────────────────────────────────
def _chrome_major() -> str:
    try:
        import subprocess
        out = subprocess.check_output(["google-chrome-stable", "--version"], stderr=subprocess.DEVNULL, text=True)
        m = re.search(r"(\d+)\.", out)
        return m.group(1) if m else "148"
    except Exception:
        return "148"


# ── Proxy pool (anti-plenger: satu proxy per akun, rotate) ───────────────────
PROXY_POOL: list[str] = []

def _normalize_proxy_entry(raw: str) -> str | None:
    """Normalize proxy string ke URL Playwright: support host:port:user:pass dan http://user:pass@host:port."""
    raw = (raw or "").strip()
    if not raw or raw.startswith("#"):
        return None
    if "://" in raw:  # sudah URL penuh
        return raw
    p = raw.split(":")
    if len(p) == 4:
        host, port, user, password = p
        return f"http://{user}:{password}@{host}:{port}"
    if len(p) == 2:
        return f"http://{p[0]}:{p[1]}"
    return None

def load_proxy_pool(path: str | None = None) -> list[str]:
    """Load pool dari V2_PROXY_FILE (atau ./proxies.txt). Return list URL."""
    p = path or PROXY_FILE or _env("V2_PROXY_FILE", str(_ROOT / "proxies.txt"))
    fp = Path(os.path.expanduser(p))
    out: list[str] = []
    if fp.is_file():
        for line in fp.read_text(errors="replace").splitlines():
            u = _normalize_proxy_entry(line)
            if u and u not in out:
                out.append(u)
    return out

_proxy_idx = 0
CURRENT_PROXY: str | None = None  # proxy yang sedang dipakai run ini (untuk IMAP OTP)
BURNED_PROXIES: set = set()  # proxy yg gagal (Turnstile/givenName) — skip di putaran berikutnya

def next_proxy(skip_burned: bool = True) -> str | None:
    """Round-robin ambil proxy berikutnya dari pool, skip yg sudah burned."""
    global _proxy_idx, CURRENT_PROXY
    if not PROXY_POOL:
        CURRENT_PROXY = PROXY or None
        return CURRENT_PROXY
    for _ in range(len(PROXY_POOL) * 2):
        px = PROXY_POOL[_proxy_idx % len(PROXY_POOL)]
        _proxy_idx += 1
        if skip_burned and px in BURNED_PROXIES:
            continue
        CURRENT_PROXY = px
        return px
    CURRENT_PROXY = None
    return None


def _launch_ctx(p, load_extension: bool = True):
    args = [
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--disable-blink-features=AutomationControlled",
        "--window-size=1280,1024",
    ]
    ignore_default = ["--enable-automation"]
    if load_extension and TS_PATCH_ENABLED:
        ext = str(TS_DIR)
        args += [f"--load-extension={ext}", f"--disable-extensions-except={ext}"]
    kwargs = dict(
        user_data_dir=str(Path(tempfile.gettempdir()) / f"v2-grok-{int(time.time())}"),
        headless=HEADLESS,
        channel="chrome",
        args=args,
        viewport={"width": 1280, "height": 1024},
        ignore_default_args=ignore_default,
    )
    px = next_proxy()  # rotate pool: satu proxy per akun (anti-plenger)
    if px:
        # WebShare: JANGAN pakai URL-embedded creds di `server` (x ERR_INVALID_AUTH_CREDENTIALS).
        # Wajib `server` bare + username/password terpisah (split-creds). Tested OK 2026-08-07.
        server = _proxy_server_bare(px)
        kwargs["proxy"] = {
            "server": server,
            "username": _proxy_user(px),
            "password": _proxy_pass(px),
        }
    print(f"[ctx] proxy: {_proxy_ip(px) if px else 'NONE'}", flush=True)
    return p.chromium.launch_persistent_context(**kwargs)


def _proxy_server_bare(url: str) -> str:
    """Kembalikan server proxy TANPA creds dari URL http://user:pass@host:port."""
    try:
        from urllib.parse import urlparse
        u = urlparse(url)
        host = u.hostname or ""
        port = u.port or 80
        return f"http://{host}:{port}"
    except Exception:
        return url


def _proxy_user(url: str) -> str | None:
    try:
        from urllib.parse import urlparse
        return urlparse(url).username or None
    except Exception:
        return None


def _proxy_pass(url: str) -> str | None:
    try:
        from urllib.parse import urlparse
        return urlparse(url).password or None
    except Exception:
        return None


def _setup_ctx(ctx, chrome_v: str):
    """Fingerprint + init-script. Mirrors dzDev37; applies to all frames incl cross-origin CF."""
    ctx.add_init_script(
        """
        function getRandomInt(min, max) { return Math.floor(Math.random() * (max - min + 1)) + min; }
        try {
            Object.defineProperty(MouseEvent.prototype, 'screenX', { value: getRandomInt(800, 1200) });
            Object.defineProperty(MouseEvent.prototype, 'screenY', { value: getRandomInt(400, 600) });
        } catch(e) {}
        """
    )


# ── Signup flow (Audit §4 + dzDev37 flow) ─────────────────────────────────────
_RUN_START_TS = time.time()  # set saat import; dipakai read_otp utk skip OTP lama

# ── Vision CAPTCHA solver (interactive Turnstile) ─────────────────────────────
_VISION_TURNSTILE_PROMPT = """You are looking at a full-page browser screenshot that may show a Cloudflare
Turnstile interactive challenge (image selection puzzle, not a simple checkbox).

If you see a visual challenge (select all images with X, click objects, crosswalks, etc.):
1. Identify the tiles/objects to click
2. Return click coordinates as percentages of the FULL PAGE screenshot:
   CLICK: x1%,y1% | x2%,y2% | ...
   where x and y are 0-100 relative to full image.

If only a simple "Verify you are human" checkbox visible:
  return exactly: CHECKBOX

If no captcha/challenge is visible:
  return exactly: NO_CAPTCHA

Do not invent coordinates for form fields."""


def _call_vision_model(image_b64: str, prompt: str, timeout: int = 60) -> str | None:
    """Call vision model via 9router (curl + browser UA utk hindari CF 1010)."""
    import base64
    if not CAPTCHA_API_KEY:
        print("[captcha] no CAPTCHA_API_KEY", flush=True)
        return None
    import random
    payload = {
        "model": CAPTCHA_MODEL,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
        ]}],
        "max_tokens": 300,
        "temperature": 0,
    }
    pf = f"/tmp/captcha_req_{int(time.time())}_{random.randrange(9999)}.json"
    try:
        with open(pf, "w") as f:
            json.dump(payload, f)
        cmd = [
            "curl", "-s", "--max-time", str(timeout),
            CAPTCHA_API_URL,
            "-H", f"Authorization: Bearer {CAPTCHA_API_KEY}",
            "-H", "Content-Type: application/json",
            "-H", "User-Agent: Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/148.0 Safari/537.36",
            "--data", f"@{pf}",
        ]
        import subprocess
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 5)
        out = r.stdout or ""
        try:
            d = json.loads(out)
            return d.get("choices", [{}])[0].get("message", {}).get("content", "")
        except Exception:
            return out
    except Exception as e:
        print(f"[captcha] vision err: {e}", flush=True)
        return None
    finally:
        try:
            os.remove(pf)
        except Exception:
            pass


def _parse_vision_clicks(text: str) -> list:
    if not text:
        return []
    upper = text.strip().upper()
    if "NO_CAPTCHA" in upper or "CHECKBOX" in upper:
        return []
    clicks = []
    for m in re.finditer(r"(\d{1,3}(?:\.\d+)?)\s*%\s*[, ]\s*(\d{1,3}(?:\.\d+)?)\s*%", text):
        x, y = float(m.group(1)), float(m.group(2))
        if 0 <= x <= 100 and 0 <= y <= 100:
            clicks.append((x, y))
    return clicks


def _solve_turnstile(page, max_wait: int = 60) -> bool:
    """Solve Turnstile: klik checkbox, lalu vision utk interactive puzzle."""
    import time as _t
    import base64 as _b64
    if not CAPTCHA_API_KEY:
        # tanpa vision — hanya klik checkbox (retry terbatas)
        return _try_click_turnstile(page, attempts=5)
    deadline = time.time() + max_wait
    while time.time() < deadline:
        # cek token sudah ada
        try:
            tok = page.evaluate("document.querySelector('input[name=cf-turnstile-response]')?.value || ''")
        except Exception:
            tok = ""
        if tok:
            return True
        # 1) klik checkbox di iframe cloudflare
        clicked = _try_click_turnstile(page, attempts=2)
        _t.sleep(2.0)
        try:
            tok = page.evaluate("document.querySelector('input[name=cf-turnstile-response]')?.value || ''")
        except Exception:
            tok = ""
        if tok:
            return True
        # 2) vision utk interactive challenge — PANGGIL SELALU kalau token belum ada
        # (sebelumnya hanya kalau checkbox tidak ketemu → vision skip saat checkbox
        #  ketemu tp token tak generate (interactive puzzle). Fix: vision selalu.)
        try:
            img = page.screenshot(full_page=False)
            b64 = _b64.b64encode(img).decode()
            resp = _call_vision_model(b64, _VISION_TURNSTILE_PROMPT)
            if resp:
                up = resp.strip().upper()
                print(f"[captcha] vision: {resp[:120]}", flush=True)
                if "NO_CAPTCHA" in up:
                    try:
                        tok = page.evaluate("document.querySelector('input[name=cf-turnstile-response]')?.value || ''")
                    except Exception:
                        tok = ""
                    if tok:
                        return True
                if "CHECKBOX" in up:
                    _try_click_turnstile(page, attempts=2)
                    _t.sleep(2.0)
                    continue
                coords = _parse_vision_clicks(resp)
                if coords:
                    try:
                        size = page.evaluate("() => ({w: Math.max(document.documentElement.scrollWidth, window.innerWidth), h: Math.max(document.documentElement.scrollHeight, window.innerHeight)})")
                    except Exception:
                        size = {"w": 1280, "h": 1024}
                    w, h = size["w"], size["h"]
                    for px, py in coords:
                        try:
                            page.mouse.click((px / 100.0) * w, (py / 100.0) * h)
                        except Exception:
                            pass
                        _t.sleep(0.4)
                    _t.sleep(2.0)
                    continue
        except Exception as e:
            print(f"[captcha] vision fail: {e}", flush=True)
        _t.sleep(1.2)
    return False


def _try_click_turnstile(page, attempts: int = 3) -> bool:
    """Klik checkbox Turnstile di iframe challenges.cloudflare.com."""
    import time as _t
    for _ in range(attempts):
        for fr in page.frames:
            if "challenges.cloudflare.com" in (fr.url or "") or "turnstile" in (fr.url or ""):
                try:
                    cb = fr.locator('input[type="checkbox"]')
                    if cb.count():
                        cb.first.click(timeout=3000)
                        return True
                except Exception:
                    pass
                try:
                    fr.locator("body").click(position={"x": 20, "y": 20}, timeout=2000)
                    return True
                except Exception:
                    pass
                break
        _t.sleep(1.0)
    return False


def _signup_one(ctx, chrome_v: str) -> dict:
    page = ctx.new_page()
    try:
        page.set_extra_http_headers(
            {
                "sec-ch-ua": f'"Chromium";v="{chrome_v}", "Google Chrome";v="{chrome_v}", "Not-A.Brand";v="99"',
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"Linux"',
                "accept-language": "en-US,en;q=0.9",
            }
        )
        # 1 open
        page.goto(SIGNUP_URL, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(3000)
        try:
            page.get_by_role("button", name="Accept All Cookies").click(timeout=3000)
        except Exception:
            pass
        # 2 email form
        page.get_by_text("Sign up with email").click(timeout=8000)
        page.wait_for_selector("input[type=email]", timeout=8000)
        # pilih sumber email: MAILLDEZ temp-mail ATAU generate random@catch-all-domain + IMAP
        if MAILLDEZ:
            mail = TempMail(MAILLDEZ)
            addr = mail.create()
        elif IMAP_USER and _env("GROK_EMAIL_DOMAINS"):
            import random as _r
            import string as _st
            domains = _env("GROK_EMAIL_DOMAINS").replace(" ", "").split(",")
            local = "".join(_r.choices(_st.ascii_lowercase + _st.digits, k=12))
            addr = f"{local}@{_r.choice(domains)}"
            mail = None
            print(f"[otp] pakai IMAP Gmail + catch-all domain: {addr}", flush=True)
        else:
            raise RuntimeError("butuh V2_MAILLDEZ_URL ATAU GROK_IMAP_* + GROK_EMAIL_DOMAINS")
        page.locator("input[type=email]").fill(addr)
        page.locator("input[type=email]").press("Enter")
        try:
            page.wait_for_selector("input[name=code]", timeout=20000)
        except Exception:
            try:
                page.get_by_role("button", name="Sign up").click(timeout=3000)
                page.wait_for_selector("input[name=code]", timeout=15000)
            except Exception:
                raise RuntimeError("could not reach OTP screen")
        # 3 OTP — ambil via MAILLDEZ atau IMAP Gmail (via proxy yang sama, anti-block)
        if mail is not None:
            code = mail.wait_code(120)
        else:
            code = read_otp_from_imap(addr, timeout=120, proxy_url=CURRENT_PROXY,
                                      since_ts=_RUN_START_TS)
        if not code:
            raise RuntimeError("OTP timeout 120s")
        print(f"[otp] OTP received: {code}", flush=True)
        # x.ai OTP UI = 6 kotak terpisah (input[maxlength="1"]), butuh waktu render.
        # WAIT dulu: tunggu 6 kotak muncul, lalu fill per-char.
        # Jika tidak, fill hidden input name=code (yang mungkin tidak diproses React).
        import time as _t
        _t.sleep(3)
        otp_chars = re.sub(r"[^A-Za-z0-9]", "", code.upper())
        # Strategi farm.py (proven): click kotak pertama, lalu KEYBOARD type seluruh
        # sequence → React auto-advance ke 6 kotak. JANGAN pakai .fill() (gagal utk
        # kotak OTP React yang tidak input biasa).
        boxes = page.locator('input[maxlength="1"]')
        _ok_box = False
        try:
            n = boxes.count()
            if n >= 1:
                _ok_box = True
        except Exception:
            n = 0
        if not _ok_box:
            # coba cari penerima OTP lain (name=code atau autocomplete)
            code_inp = page.locator('input[name="code"], input[autocomplete="one-time-code"]')
            if code_inp.count():
                try:
                    code_inp.first.click(timeout=2000, force=True)
                    _t.sleep(0.05)
                    page.keyboard.press("Control+a"); page.keyboard.press("Backspace")
                    page.keyboard.type(otp_chars, delay=40)
                    print("[otp] OTP typed via name=code keyboard", flush=True)
                except Exception as e:
                    print(f"[otp] name=code keyboard fail: {e}", flush=True)
                    page.keyboard.type(otp_chars, delay=40)
        else:
            # 6 boxes via keyboard typing
            for _k in range(3):
                try:
                    boxes.first.click(timeout=1200, force=True)
                    _t.sleep(0.05)
                    page.keyboard.press("Control+a"); page.keyboard.press("Backspace")
                    page.keyboard.type(otp_chars[:6], delay=40)
                    _t.sleep(0.2)
                    # verify
                    vals = []
                    for _b in range(min(6, boxes.count())):
                        try:
                            vals.append(boxes.nth(_b).input_value())
                        except Exception:
                            vals.append("?")
                    print(f"[otp] OTP typed boxes: {''.join(vals)}", flush=True)
                    if "".join(vals).upper() == otp_chars[:6]:
                        break
                except Exception as e:
                    print(f"[otp] OTP box typing warn: {e}", flush=True)
                _t.sleep(0.3)
        _t.sleep(0.5)
        # klik Confirm email / Enter
        try:
            page.get_by_role("button", name="Confirm email").click(timeout=5000)
            print("[otp] Confirmed via Confirm email btn", flush=True)
        except Exception:
            try:
                page.get_by_role("button", name="Confirm").click(timeout=3000)
            except Exception:
                page.keyboard.press("Enter")
        print(f"[otp] OTP submitted", flush=True)
        # Wait longer for "Complete your sign up" page to fully load
        try:
            page.wait_for_selector("input[name=givenName]", timeout=30000)
        except Exception:
            # Fallback: check if we're on the right URL or look for any input box
            print("[otp] givenName not found in 30s, checking current page...", flush=True)
            page.screenshot(path="screenshot_after_otp.png", timeout=30000)
            # Check if on grok.com already (success redirect)
            if "grok.com" in page.url:
                print("[otp] ALREADY ON grok.com - registration complete!", flush=True)
                return {
                    "email": addr,
                    "password": PASSWORD,
                    "status": "redirected",
                    "final_url": page.url,
                    "timestamp": int(time.time()),
                }
            raise RuntimeError("OTP success but couldn't find givenName field")
        # 4 name + password
        local = addr.split("@")[0]
        parts = re.split(r"[._\-]", local)
        given = (parts[0] or "John").capitalize()
        family = (parts[1] if len(parts) > 1 else "Doe").capitalize()
        page.locator("input[name=givenName]").fill(given)
        page.locator("input[name=familyName]").fill(family)
        page.locator("input[name=password]").fill(PASSWORD)
        # 5 turnstile — click checkbox + VISION solver utk interactive puzzle
        import time as _t2
        # dismiss cookie banner dulu (OneTrust) — checkbox-nya ngaco dengan Turnstile
        try:
            page.locator("#onetrust-accept-btn-handler").click(timeout=2000, force=True)
        except Exception:
            try:
                page.get_by_role("button", name="Accept All").click(timeout=2000)
            except Exception:
                pass
        _t2.sleep(0.5)
        tok = ""
        solved = _solve_turnstile(page, max_wait=60)
        try:
            tok = page.evaluate("document.querySelector('input[name=cf-turnstile-response]')?.value || ''")
        except Exception:
            tok = ""
        if not tok:
            page.screenshot(path="screenshot_turnstile_fail.png")
            raise RuntimeError("Turnstile timeout (checkbox tidak bisa di-solve otomatis)")
        print("[turnstile] token OK", flush=True)
        try:
            page.get_by_role("button", name="Complete sign up").click(timeout=5000)
        except Exception:
            page.keyboard.press("Enter")
        # 6 redirect to grok.com or detect SSO
        redirected = False
        for _ in range(30):
            page.wait_for_timeout(2000)
            try:
                if "grok.com" in page.url:
                    redirected = True
                    break
            except Exception:
                continue
        sso_cookies = [c for c in page.context.cookies() if "sso" in c.get("name", "").lower()]
        if not redirected and not sso_cookies:
            raise RuntimeError(f"no redirect (last: {page.url})")
        data = {
            "email": addr,
            "password": PASSWORD,
            "code": code,
            "sso_cookies": page.context.cookies(),
            "final_url": page.url,
            "timestamp": int(time.time()),
        }
        # ── KRITIS: ambil OAuth token SEKARANG (masih proxy + session login) ──
        # Setelah farm, IP proxy berubah / CF block → momen inilah satu-satunya
        # window utk dapat access_token sebelum inject ke 9Router.
        try:
            oauth = _obtain_oauth_token(page)
            if oauth:
                data["access_token"] = oauth.get("access_token")
                data["refresh_token"] = oauth.get("refresh_token")
                data["token_expires_in"] = oauth.get("expires_in")
                print(f"[oauth] TOKEN diperoleh utk {addr} (expires_in={oauth.get('expires_in')})", flush=True)
                # ── AUTO-INJECT per akun (V2_AUTO_INJECT=1) ──
                if AUTO_INJECT:
                    try:
                        inj = inject_to_9router(data["access_token"], addr, CURRENT_PROXY)
                        if inj.get("ok"):
                            print(f"[inject] OK -> conn {inj.get('conn_id')} pool {inj.get('pool_id','')}", flush=True)
                            data["injected"] = True
                            data["conn_id"] = inj.get("conn_id")
                            data["pool_id"] = inj.get("pool_id")
                        else:
                            print(f"[inject] FAIL: {inj.get('error')}", flush=True)
                            data["injected"] = False
                            data["inject_error"] = str(inj.get("error"))[:200]
                    except Exception as e:
                        print(f"[inject] warn: {e}", flush=True)
                        data["injected"] = False
                        data["inject_error"] = str(e)[:200]
            else:
                print(f"[oauth] token tidak diperoleh utk {addr}", flush=True)
        except Exception as e:
            print(f"[oauth] warn: {e}", flush=True)
            try:
                data["oauth_error"] = str(e)[:200]
            except Exception:
                pass
        return data
    finally:
        page.close()


# ── Router add (device-code consent automation) ────────────────────────────────
def _proxy_ip(proxy_url: str | None) -> str:
    if not proxy_url:
        return ""
    try:
        from urllib.parse import urlparse
        return urlparse(proxy_url).hostname or ""
    except Exception:
        return ""


def _r9_req(method: str, path: str, body=None):
    import urllib.request
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{R9_BASE}{path}", data=data, method=method)
    req.add_header("x-9r-cli-token", R9_TOKEN)
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", "Mozilla/5.0 (X11; Linux x86_64) Chrome/148.0 Safari/537.36")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {"error": e.reason}
    except Exception as e:
        return -1, {"error": str(e)}


def _find_pool_for_ip(pools, proxy_ip: str):
    if not proxy_ip or not pools:
        return None
    for p in pools:
        data = p.get("data") or p
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except Exception:
                continue
        if isinstance(data, dict):
            name = data.get("name", "")
            pool_url = data.get("proxyUrl", "")
            if name.startswith("Imported") and proxy_ip in name:
                return p["id"]
            if proxy_ip in pool_url:
                return p["id"]
    return None


def inject_to_9router(access_token: str, email: str, proxy_url: str | None = None) -> dict:
    """Inject 1 akun ke 9Router (grok-cli) + assign proxy pool match IP farm."""
    import json as _json
    if not R9_TOKEN:
        return {"ok": False, "error": "R9_TOKEN kosong"}
    st, res = _r9_req("POST", "/api/oauth/grok-cli/exchange", {"code": access_token})
    if st != 200 or not res.get("success"):
        return {"ok": False, "error": res}
    conn = res.get("connection", {})
    conn_id = conn.get("id")
    out = {"ok": True, "conn_id": conn_id, "provider": conn.get("provider")}

    proxy_ip = _proxy_ip(proxy_url)
    try:
        spools, pools = _r9_req("GET", "/api/proxy-pools")
        pool_id = None
        if spools == 200:
            raw = pools.get("proxyPools") or pools.get("pools") or pools.get("data") or []
            pool_id = _find_pool_for_ip(raw, proxy_ip) if proxy_ip else None
        if pool_id:
            _r9_req("PUT", f"/api/providers/{conn_id}", {"proxyPoolId": pool_id})
            out["pool_id"] = pool_id
        else:
            if spools == 200:
                raw = pools.get("proxyPools") or pools.get("data") or []
                for p0 in raw:
                    d0 = p0.get("data") or p0
                    if isinstance(d0, str):
                        try:
                            d0 = _json.loads(d0)
                        except Exception:
                            d0 = {}
                    if isinstance(d0, dict) and d0.get("name", "").startswith("Imported"):
                        _r9_req("PUT", f"/api/providers/{conn_id}", {"proxyPoolId": p0["id"]})
                        out["pool_id"] = p0["id"]
                        break
    except Exception as e:
        out["pool_warn"] = str(e)[:120]
    return out


def _obtain_oauth_token(page) -> dict | None:
    """Ambil OAuth access token via device-code, DI browser yang sama (masih session login).

    Wajib dilakukan saat session masih aktif & proxy sama (window setalah signup).
    Hinari CF block yang muncul belakangan di IP proxy."""
    import urllib.request, urllib.parse
    XAI_CLIENT_ID = "b1a00492-073a-47ea-816f-4c329264a828"
    scope = "openid profile email offline_access grok-cli:access api:access conversations:read conversations:write"

    # 1) request device code — via urllib server-side (bukan page fetch — CORS block)
    import urllib.request, urllib.parse
    XAI_CLIENT_ID = "b1a00492-073a-47ea-816f-4c329264a828"
    scope = "openid profile email offline_access grok-cli:access api:access conversations:read conversations:write"
    try:
        dc_body = urllib.parse.urlencode({
            "client_id": XAI_CLIENT_ID, "scope": scope, "referrer": "grok-build",
        }).encode()
        dc_req = urllib.request.Request("https://auth.x.ai/oauth2/device/code", data=dc_body, method="POST")
        dc_req.add_header("Content-Type", "application/x-www-form-urlencoded")
        dc_req.add_header("User-Agent", "Mozilla/5.0")
        with urllib.request.urlopen(dc_req, timeout=20) as r:
            dc = json.loads(r.read().decode())
    except Exception as e:
        print(f"[oauth] device-code fail: {e}", flush=True)
        return None
    if not dc or not dc.get("device_code"):
        print(f"[oauth] device-code gmau: {dc}", flush=True)
        return None
    print(f"[oauth] user_code={dc.get('user_code')}", flush=True)

    # 2) buka verification page di tab baru (session & proxy masih sama)
    verify_url = dc.get("verification_uri_complete")
    try:
        vpage = page.context.new_page()
        vpage.goto(verify_url, wait_until="domcontentloaded", timeout=45000)
        vpage.wait_for_timeout(4000)
        # auto-approve
        try:
            vpage.get_by_role("button", name="Continue", exact=False).click(timeout=8000)
            vpage.wait_for_timeout(2500)
        except Exception:
            pass
        try:
            vpage.get_by_role("button", name="Allow", exact=True).click(timeout=8000)
            vpage.wait_for_timeout(1500)
        except Exception:
            try:
                vpage.get_by_role("button", name="Allow All", exact=True).click(timeout=4000)
            except Exception:
                pass
        vpage.close()
    except Exception as e:
        print(f"[oauth] approve page warning: {str(e)[:100]}", flush=True)

    # 3) poll token — via urllib server-side
    import time as _t3
    for _ in range(24):
        try:
            tok_body = urllib.parse.urlencode({
                "client_id": XAI_CLIENT_ID,
                "device_code": dc["device_code"],
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            }).encode()
            tok_req = urllib.request.Request("https://auth.x.ai/oauth2/token", data=tok_body, method="POST")
            tok_req.add_header("Content-Type", "application/x-www-form-urlencoded")
            tok_req.add_header("User-Agent", "Mozilla/5.0")
            with urllib.request.urlopen(tok_req, timeout=20) as r:
                tr = json.loads(r.read().decode())
        except Exception as e:
            print(f"[oauth] poll fail: {e}", flush=True)
            break
        if tr.get("access_token"):
            return tr
        if tr.get("error") not in ("authorization_pending", "slow_down"):
            print(f"[oauth] poll err: {tr}", flush=True)
            return None
        _t3.sleep(dc.get("interval", 5) or 5)
    print("[oauth] token poll timeout", flush=True)
    return None


def _add_to_router(ctx, accounts: list[dict]):
    if not ROUTER9_URL or not accounts:
        return 0, 0, 0
    r9 = NineRouter(ROUTER9_URL, ROUTER9_PASS)
    if not r9.login():
        print("[router] login failed"); return 0, 0, len(accounts)
    existing = set()
    try:
        conns = creq.get(f"{ROUTER9_URL.rstrip('/')}/api/providers", timeout=15).json().get("connections", [])
        existing = {c.get("email") for c in conns if c.get("provider") == "grok-cli"}
    except Exception:
        pass
    added = skipped = failed = 0
    for acc in accounts:
        email = acc["email"]
        if email in existing:
            skipped += 1; continue
        try:
            for c in acc.get("sso_cookies", []):
                cc = dict(c)
                if not cc.get("domain"):
                    continue
                if cc.get("sameSite") not in ("Strict", "Lax", "None"):
                    cc["sameSite"] = "Lax"
                ctx.add_cookies([cc])
            d = r9.device_code()
            page = ctx.new_page()
            page.goto(d["verification_uri_complete"], wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(3000)
            has_login = page.evaluate("!!document.querySelector('input[type=email], input[type=password]')")
            if has_login:
                print(f"[router] {email}: SSO expired"); failed += 1; page.close(); continue
            try:
                page.get_by_role("button", name="Continue", exact=False).click(timeout=5000)
                page.wait_for_timeout(3000)
            except Exception:
                pass
            try:
                page.get_by_role("button", name="Allow", exact=True).click(timeout=8000)
            except Exception:
                try:
                    page.get_by_role("button", name="Allow All", exact=True).click(timeout=3000)
                except Exception:
                    print(f"[router] {email}: Allow not found"); failed += 1; page.close(); continue
            page.wait_for_timeout(2000)
            page.close()
            ok = False
            for _ in range(60):
                res = r9.poll(d["device_code"], d["codeVerifier"])
                if res.get("success"):
                    ok = True; break
                if not res.get("pending"):
                    break
                time.sleep(5)
            added += 1 if ok else 0
            failed += 0 if ok else 1
        except Exception as e:
            print(f"[router] {email}: {e}"); failed += 1
    return added, skipped, failed


# ── Main ───────────────────────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser(description="Grok farm v2 (new method)")
    ap.add_argument("count", nargs="?", type=int, default=1)
    ap.add_argument("--dry-run", action="store_true", help="validate flow without creating accounts")
    ap.add_argument("--router", action="store_true", help="add accounts from OUT to 9Router")
    args = ap.parse_args()

    if args.router:
        accounts = [json.loads(l) for l in OUT.read_text().splitlines() if l.strip()]
        with sync_playwright() as p:
            ctx = _launch_ctx(p, load_extension=False)
            added, skipped, failed = _add_to_router(ctx, accounts)
            ctx.close()
        print(f"router: added={added} skipped={skipped} failed={failed}")
        return 0

    # sanity: turnstilePatch present (kalau patch diaktifkan)
    if TS_PATCH_ENABLED and not ((TS_DIR / "manifest.json").exists() and (TS_DIR / "script.js").exists()):
        print(f"FAIL: turnstilePatch missing at {TS_DIR} (V2_TURNSTILE_PATCH=1 tapi file gak ada)")
        return 1
    if args.dry_run:
        print("DRY-RUN: flow validasi tanpa bikin akun.")
        print(f"  turnstilePatch : {TS_DIR} {'AKTIF (V2_TURNSTILE_PATCH=1)' if TS_PATCH_ENABLED else '(fallback saja — default off)'}")
        print(f"  device endpoint: {XAI_DEVICE_CODE}")
        print(f"  scope          : {XAI_SCOPE}")
        # dry validate device-code endpoint reachable (no account created)
        try:
            d = xai_device_code()
            print(f"  device-code OK : user_code={d['user_code']} expires_in={d['expires_in']}")
        except Exception as e:
            print(f"  device-code FAIL: {e}")
            return 1
        print("DRY-RUN OK — siap eksekusi asli.")
        return 0

    chrome_v = _chrome_major()
    results: list = []
    global PROXY_POOL
    PROXY_POOL = load_proxy_pool()
    if PROXY_POOL:
        print(f"[farm] proxy pool: {len(PROXY_POOL)} proxy (round-robin per akun)")
    elif PROXY:
        print("[farm] proxy tunggal (V2_PROXY)")
    else:
        print("[farm] WARN: tanpa proxy — langsung dari IP VPS (risiko flag tinggi)")
    with sync_playwright() as p:
        for i in range(args.count):
            t0 = time.time()
            # ── KRITIS: context PER AKUN (proxy rotate per akun!) ──
            # Satu context untuk semua akun = semua pakai proxy pertama =
            # xAI flag setelah akun ke-1/2. Context baru + close per akun.
            ok = False
            attempts = 0
            while attempts < 3 and not ok:
                attempts += 1
                px_now = CURRENT_PROXY  # proxy yang dipakai attempt ini
                ctx = _launch_ctx(p, load_extension=True)
                _setup_ctx(ctx, chrome_v)
                try:
                    res = _signup_one(ctx, chrome_v)
                    results.append(res)
                    with OUT.open("a") as f:
                        f.write(json.dumps(res) + "\n")
                    print(f"[{i+1}] OK {res['email']} ({time.time()-t0:.1f}s, attempt {attempts})")
                    ok = True
                except Exception as e:
                    emsg = str(e)
                    print(f"[{i+1}] FAIL attempt {attempts}: {emsg} ({time.time()-t0:.1f}s)")
                    # burn proxy jika gagal karena Turnstile / givenName / OTP — kemungkinan IP di-flag
                    if ("Turnstile" in emsg or "givenName" in emsg or "OTP" in emsg or "invalid" in emsg.lower()) and px_now:
                        BURNED_PROXIES.add(px_now)
                        print(f"[{i+1}] burn proxy {_proxy_ip(px_now)} (burned={len(BURNED_PROXIES)})")
                    # email mungkin sudah dipakai di attempt 1 — generate ulang di retry
                    time.sleep(3)
                finally:
                    try:
                        ctx.close()
                    except Exception:
                        pass
            if not ok:
                print(f"[{i+1}] GAGAL SETELAH {attempts} ATTEMPT — skip akun")
            # pause antar akun (rate-limit friendly)
            time.sleep(2)
    if results and args.router:
        with sync_playwright() as p:
            ctx = _launch_ctx(p, load_extension=False)
            added, skipped, failed = _add_to_router(ctx, results)
            print(f"router: added={added} skipped={skipped} failed={failed}")
            ctx.close()
    print(f"DONE: ok={len([r for r in results if 'email' in r])} total={args.count}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
