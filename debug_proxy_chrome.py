#!/usr/bin/env python3
"""debug_proxy_chrome.py — tes 3 cara setup proxy Playwright+Chrome untuk WebShare."""
import sys, time
sys.path.insert(0, "/home/grok/grok-farm")
import farm_v2
from playwright.sync_api import sync_playwright

farm_v2.PROXY_POOL = farm_v2.load_proxy_pool()
px = farm_v2.next_proxy()
print("PROXY:", px, flush=True)
# parse
from urllib.parse import urlparse
u = urlparse(px)
phost = u.hostname
pport = u.port
puser = u.username or ""
ppass = u.password or ""
print(f"parsed: {phost}:{pport} user={puser}", flush=True)

def try_launch(label, **extra):
    try:
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                user_data_dir=f"/tmp/px-{label}-{int(time.time())}",
                headless=True,
                channel="chrome",
                args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled"],
                ignore_default_args=["--enable-automation"],
                viewport={"width": 1280, "height": 1024},
                **extra,
            )
            page = ctx.new_page()
            page.goto("https://api.ipify.org", timeout=25000)
            body = page.inner_text("body") if page.locator("body").count() else "?"
            print(f"[{label}] OK ip={body}", flush=True)
            ctx.close()
            return True
    except Exception as e:
        print(f"[{label}] FAIL {str(e)[:120]}", flush=True)
        return False

# varian 1: proxy dict server full URL (sudah dicoba, gagal)
# varian 2: proxy server bare (no auth) + args --proxy-server dengan creds di flag
try_launch("server-only", proxy={"server": px})
# varian 3: chrome args proxy-server (creds di flag)
try_launch(
    "chrome-flag",
    args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled",
          f"--proxy-server=http://{phost}:{pport}",
          f"--proxy-bypass-list=<-loopback>"],
    ignore_default_args=["--enable-automation"],
)
# varian 4: proxy + credentials dict (server bare + username/password)
try_launch("split-creds", proxy={"server": f"http://{phost}:{pport}", "username": puser, "password": ppass})
