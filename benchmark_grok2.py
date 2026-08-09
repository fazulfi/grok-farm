#!/usr/bin/env python3
"""benchmark_grok2.py — tes akun Grok via OAuth device-code (Bearer token).

Langkah:
1) Request device code (auth.x.ai)
2) Approve otomatis pake SSO cookies (browser headless: buka verification_uri_complete, klik Continue/Allow)
3) Poll token → dapat access_token
4) Call /v1/models, reasoning, usage dengan Bearer
"""
import json
import sys
import time
import urllib.request

sys.path.insert(0, "/home/grok/grok-farm")
import farm_v2
from playwright.sync_api import sync_playwright

XAI_CLIENT_ID = "b1a00492-073a-47ea-816f-4c329264a828"
XAI_SCOPE = "openid profile email offline_access grok-cli:access api:access conversations:read conversations:write"


def load_account(path="/home/grok/grok-farm/v2_sso.txt"):
    with open(path) as f:
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
    raise RuntimeError("no valid account")


def req_json(url, data=None, headers=None, method="POST", form=False):
    if form and isinstance(data, str):
        body = data.encode()
    elif data is not None:
        body = json.dumps(data).encode()
    else:
        body = None
    h = {"Content-Type": "application/x-www-form-urlencoded", "User-Agent": "Mozilla/5.0"}
    if headers:
        h.update(headers)
    r = urllib.request.Request(url, data=body, method=method, headers=h)
    try:
        with urllib.request.urlopen(r, timeout=25) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {"error": e.reason}


def cookie_list(cookies):
    out = []
    for c in cookies:
        if not c.get("name") or not c.get("value"):
            continue
        cc = dict(c)
        if cc.get("sameSite") not in ("Strict", "Lax", "None"):
            cc["sameSite"] = "Lax"
        out.append(cc)
    return out


def main():
    acc = load_account()
    print("AKUN:", acc["email"], flush=True)
    farm_v2.PROXY_POOL = farm_v2.load_proxy_pool()
    px = farm_v2.next_proxy()
    print("PROXY:", (px or "none")[:50], flush=True)

    # 1) device code
    st, dc = req_json(
        "https://auth.x.ai/oauth2/device/code",
        data=f"client_id={XAI_CLIENT_ID}&scope={XAI_SCOPE.replace(' ', '+')}&referrer=grok-build",
        form=True,
    )
    print("device_code status:", st, flush=True)
    print("user_code:", dc.get("user_code"), "verify:", dc.get("verification_uri_complete"), flush=True)
    if st != 200:
        print("FAIL device code:", dc); return

    # 2) approve via browser (SSO cookies sudah login)
    with sync_playwright() as p:
        kwargs = {"server": farm_v2._proxy_server_bare(px),
                  "username": farm_v2._proxy_user(px), "password": farm_v2._proxy_pass(px)} if px else None
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=f"/tmp/bench-{int(time.time())}", headless=True, channel="chrome",
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled"],
            ignore_default_args=["--enable-automation"],
            proxy=kwargs,
        )
        ctx.add_cookies(cookie_list(acc["sso_cookies"]))
        page = ctx.new_page()
        page.goto(dc["verification_uri_complete"], wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(3500)
        print("URL:", page.url, flush=True)
        # Cek halaman login — kalau ada form email/password, isi dengan kredensial akun
        try:
            has_email = page.locator('input[type="email"]').count() or page.locator('input[name="identifier"]').count()
            if has_email:
                print("[auth] login form ditemukan — login manual dgn kredensial akun", flush=True)
                page.locator('input[type="email"]').first.fill(acc["email"])
                page.keyboard.press("Enter")
                page.wait_for_timeout(3000)
                # password
                pw = page.locator('input[type="password"]')
                if pw.count():
                    pw.first.fill(acc["password"])
                    page.keyboard.press("Enter")
                    page.wait_for_timeout(4000)
                print("[auth] login submitted, URL:", page.url, flush=True)
        except Exception as e:
            print("[auth] login check warn:", str(e)[:100], flush=True)
        # Continue
        try:
            page.get_by_role("button", name="Continue", exact=False).click(timeout=8000)
            page.wait_for_timeout(2500)
        except Exception as e:
            print("Continue warn:", str(e)[:80], flush=True)
        # Allow
        try:
            page.get_by_role("button", name="Allow", exact=True).click(timeout=8000)
            page.wait_for_timeout(2000)
        except Exception:
            try:
                page.get_by_role("button", name="Allow All", exact=True).click(timeout=4000)
            except Exception as e:
                print("Allow warn:", str(e)[:80], flush=True)
        page.wait_for_timeout(1500)
        ctx.close()

    # 3) poll token
    print("polling token...", flush=True)
    token = None
    for _ in range(30):
        st, tr = req_json(
            "https://auth.x.ai/oauth2/token",
            data=f"client_id={XAI_CLIENT_ID}&device_code={dc['device_code']}&grant_type=urn:ietf:params:oauth:grant-type:device_code",
            form=True,
        )
        if tr.get("access_token"):
            token = tr
            break
        if tr.get("error") not in ("authorization_pending", "slow_down"):
            print("poll error:", tr, flush=True); return
        time.sleep(dc.get("interval", 5))
    if not token:
        print("FAIL: token timeout"); return
    print("TOKEN OK! expires_in:", token.get("expires_in"), flush=True)
    at = token["access_token"]
    hdr = {"Authorization": f"Bearer {at}", "User-Agent": "Mozilla/5.0"}

    # 4) models
    st, models = req_json("https://api.x.ai/v1/models", headers=hdr, method="GET")
    print("\n=== /v1/models:", st, "===", flush=True)
    if st == 200:
        ids = [m.get("id") for m in models.get("data", [])]
        print("models:", json.dumps(ids[:40]), flush=True)
    else:
        print(models, flush=True)

    # 5) reasoning
    print("\n=== REASONING ===", flush=True)
    prompt = "Solve: train 60km/h for 2h then 90km/h for 1.5h. Total distance? Show reasoning step by step. Answer concisely."
    for model in (["grok-4.5", "grok-4", "grok-3", "grok-4-fast", "grok-4.1"]):
        st, rr = req_json(
            "https://api.x.ai/v1/chat/completions",
            data={"model": model, "messages": [{"role": "user", "content": prompt}], "max_tokens": 600},
            headers=hdr,
        )
        print(f"--- {model} -> {st} ---", flush=True)
        if st == 200:
            msg = rr.get("choices", [{}])[0].get("message", {})
            print("content:", (msg.get("content") or "")[:700], flush=True)
            print("reasoning:", (msg.get("reasoning") or "")[:400], flush=True)
            print(">> MODEL OK:", model, flush=True)
            break
        else:
            print(rr, flush=True)
        time.sleep(1)

    # 6) usage
    print("\n=== USAGE ===", flush=True)
    for u in ("https://api.x.ai/v1/usage", "https://api.x.ai/v1/credits", "https://api.x.ai/v1/user/usage"):
        st, uu = req_json(u, headers=hdr, method="GET")
        print(f"{u} -> {st}: {json.dumps(uu)[:400]}", flush=True)


if __name__ == "__main__":
    main()
