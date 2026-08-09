#!/usr/bin/env python3
"""debug_device_page.py — dump DOM halaman accounts.x.ai/oauth2/device (dgn cookies akun)."""
import json, sys, time
sys.path.insert(0, "/home/grok/grok-farm")
import farm_v2
from playwright.sync_api import sync_playwright

def load_account():
    with open("/home/grok/grok-farm/v2_sso.txt") as f:
        for line in f:
            line = line.strip()
            if not line or line == "null":
                continue
            try:
                d = json.loads(line)
                if d and d.get("email"):
                    return d
            except Exception:
                continue

acc = load_account()
print("AKUN:", acc["email"], flush=True)
farm_v2.PROXY_POOL = farm_v2.load_proxy_pool()
px = farm_v2.next_proxy()
print("PROXY:", (px or "none")[:50], flush=True)

import urllib.request, urllib.parse
scope = "openid profile email offline_access grok-cli:access api:access conversations:read conversations:write"
body = urllib.parse.urlencode({"client_id": "b1a00492-073a-47ea-816f-4c329264a828", "scope": scope, "referrer": "grok-build"})
req = urllib.request.Request("https://auth.x.ai/oauth2/device/code", data=body.encode(), method="POST")
req.add_header("Content-Type", "application/x-www-form-urlencoded")
with urllib.request.urlopen(req, timeout=20) as r:
    dc = json.loads(r.read().decode())
print("user_code:", dc["user_code"], flush=True)

with sync_playwright() as p:
    kwargs = {"server": farm_v2._proxy_server_bare(px), "username": farm_v2._proxy_user(px), "password": farm_v2._proxy_pass(px)} if px else None
    ctx = p.chromium.launch_persistent_context(
        user_data_dir=f"/tmp/devpg-{int(time.time())}", headless=True, channel="chrome",
        args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled"],
        ignore_default_args=["--enable-automation"], proxy=kwargs,
    )
    for c in acc["sso_cookies"]:
        if c.get("name") and c.get("value"):
            cc = dict(c)
            if cc.get("sameSite") not in ("Strict", "Lax", "None"):
                cc["sameSite"] = "Lax"
            ctx.add_cookies([cc])
    page = ctx.new_page()
    page.goto(dc["verification_uri_complete"], wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(6000)
    print("URL:", page.url, flush=True)
    # dump semua tombol + input + teks
    btns = page.evaluate("Array.from(document.querySelectorAll('button')).map(b => (b.innerText||'').trim().slice(0,50))")
    print("BUTTONS:", btns, flush=True)
    inputs = page.evaluate("Array.from(document.querySelectorAll('input')).map(i => (i.type||'')+':'+(i.name||''))")
    print("INPUTS:", inputs, flush=True)
    body_txt = page.evaluate("document.body.innerText.slice(0, 600)").replace("\n", " | ")
    print("BODY:", body_txt, flush=True)
    page.screenshot(path="/tmp/device_page.png")
    print("screenshot saved", flush=True)
    ctx.close()
