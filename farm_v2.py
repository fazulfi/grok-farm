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
PROXY = _env("V2_PROXY", "")  # optional per-run proxy server (http://user:pass@host:port)
OUT = Path(_env("V2_OUT", str(_ROOT / "v2_sso.txt")))

SIGNUP_URL = "https://accounts.x.ai/sign-up?redirect=grok-com"
XAI_CLIENT_ID = "b1a00492-073a-47ea-816f-4c329264a828"
XAI_DEVICE_CODE = "https://auth.x.ai/oauth2/device/code"
XAI_TOKEN = "https://auth.x.ai/oauth2/token"
XAI_SCOPE = "openid profile email offline_access grok-cli:access api:access conversations:read conversations:write"

_domain_idx = 0

def _next_domain() -> str:
    global _domain_idx
    if not MAILLDEZ_DOMAINS:
        return ""
    d = MAILLDEZ_DOMAINS[_domain_idx % len(MAILLDEZ_DOMAINS)]
    _domain_idx += 1
    return d


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
        },
        headers={"User-Agent": "GrokFarm/v2 (device)"},
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
            headers={"User-Agent": "GrokFarm/v2 (device)"},
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
    if PROXY:
        kwargs["proxy"] = {"server": PROXY}
    return p.chromium.launch_persistent_context(**kwargs)


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
        if not MAILLDEZ:
            raise RuntimeError("V2_MAILLDEZ_URL required (temp-mail API)")
        mail = TempMail(MAILLDEZ)
        addr = mail.create()
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
        # 3 OTP
        code = mail.wait_code(120)
        if not code:
            raise RuntimeError("OTP timeout 120s")
        page.locator("input[name=code]").first.fill(code, timeout=15000)
        page.keyboard.press("Enter")
        page.wait_for_selector("input[name=givenName]", timeout=20000)
        # 4 name + password
        local = addr.split("@")[0]
        parts = re.split(r"[._\-]", local)
        given = (parts[0] or "John").capitalize()
        family = (parts[1] if len(parts) > 1 else "Doe").capitalize()
        page.locator("input[name=givenName]").fill(given)
        page.locator("input[name=familyName]").fill(family)
        page.locator("input[name=password]").fill(PASSWORD)
        # 5 turnstile auto-solve (extension) → cf-turnstile-response
        tok = ""
        for _ in range(40):
            tok = page.evaluate("document.querySelector('input[name=cf-turnstile-response]')?.value || ''")
            if tok:
                break
            page.wait_for_timeout(1000)
        if not tok:
            raise RuntimeError("Turnstile timeout 40s")
        page.get_by_role("button", name="Complete sign up").click()
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
        return data
    finally:
        page.close()


# ── Router add (device-code consent automation) ────────────────────────────────
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
    results = []
    with sync_playwright() as p:
        ctx = _launch_ctx(p, load_extension=True)
        _setup_ctx(ctx, chrome_v)
        for i in range(args.count):
            t0 = time.time()
            try:
                res = _signup_one(ctx, chrome_v)
                results.append(res)
                with OUT.open("a") as f:
                    f.write(json.dumps(res) + "\n")
                print(f"[{i+1}] OK {res['email']} ({time.time()-t0:.1f}s)")
            except Exception as e:
                print(f"[{i+1}] FAIL {e} ({time.time()-t0:.1f}s)")
            try:
                ctx.clear_cookies()
            except Exception:
                pass
        ctx.close()
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
