#!/usr/bin/env python3
"""debug_signup_dom.py v2 — buka signup x.ai via farm_v2._launch_ctx (split-creds proxy),
isi email, submit OTP, screenshot + dump DOM setelahnya."""
import sys, time
sys.path.insert(0, "/home/grok/grok-farm")
import farm_v2
from playwright.sync_api import sync_playwright

farm_v2.PROXY_POOL = farm_v2.load_proxy_pool()
print("POOL:", len(farm_v2.PROXY_POOL), flush=True)

with sync_playwright() as p:
    ctx = farm_v2._launch_ctx(p, load_extension=False)
    print("CTX OK, proxy:", (farm_v2.CURRENT_PROXY or "none")[:55], flush=True)
    page = ctx.new_page()
    try:
        page.goto(farm_v2.SIGNUP_URL, wait_until="domcontentloaded", timeout=45000)
    except Exception as e:
        print("GOTO FAIL:", str(e)[:150], flush=True)
        ctx.close(); sys.exit(1)
    time.sleep(3)
    page.screenshot(path="/tmp/dbg_step1.png")
    try:
        page.get_by_role("button", name="Accept All Cookies").click(timeout=3000)
    except Exception:
        pass
    try:
        page.get_by_text("Sign up with email").click(timeout=8000)
        page.wait_for_selector("input[type=email]", timeout=8000)
        print("EMAIL FORM OK", flush=True)
    except Exception as e:
        print("EMAIL FORM FAIL:", str(e)[:120], flush=True)
        page.screenshot(path="/tmp/dbg_step2.png")
        ctx.close(); sys.exit(1)
    import random as r, string as st
    local = "".join(r.choices(st.ascii_lowercase + st.digits, k=12))
    addr = f"{local}@YOURDOMAIN.com"
    print("ADDR:", addr, flush=True)
    page.locator("input[type=email]").fill(addr)
    page.locator("input[type=email]").press("Enter")
    time.sleep(6)
    page.screenshot(path="/tmp/dbg_step3.png")
    print("URL after email:", page.url, flush=True)
    code = farm_v2.read_otp_from_imap(addr, timeout=100, proxy_url=farm_v2.CURRENT_PROXY)
    print("OTP:", code, flush=True)
    if not code:
        page.screenshot(path="/tmp/dbg_no_otp.png"); ctx.close(); sys.exit(1)
    # isi OTP
    try:
        page.wait_for_selector("input[name=code]", timeout=15000)
        page.locator("input[name=code]").first.fill(code, timeout=10000)
    except Exception:
        # mungkin OTP box terpisah (6 kotak single char)
        boxes = page.locator("input[maxlength=1]")
        print("OTP boxes:", boxes.count(), flush=True)
        if boxes.count() >= 6:
            for i, ch in enumerate(code[:6]):
                boxes.nth(i).fill(ch)
        else:
            print("no OTP input found"); page.screenshot(path="/tmp/dbg_no_otp_input.png"); ctx.close(); sys.exit(1)
    page.keyboard.press("Enter")
    print("OTP SUBMITTED, waiting 12s...", flush=True)
    time.sleep(12)
    print("URL after OTP:", page.url, flush=True)
    page.screenshot(path="/tmp/dbg_step4.png", full_page=True)
    inputs = page.evaluate("Array.from(document.querySelectorAll('input')).map(i => (i.name||'') + ':' + (i.type||''))")
    print("INPUTS:", inputs, flush=True)
    btns = page.evaluate("Array.from(document.querySelectorAll('button')).map(b => (b.innerText||'').trim().slice(0,40))")[:12]
    print("BUTTONS:", btns, flush=True)
    body_txt = page.evaluate("document.body.innerText.slice(0, 800)")
    print("BODY TEXT:", body_txt.replace("\n", " | ")[:800], flush=True)
    ctx.close()
print("DONE", flush=True)
