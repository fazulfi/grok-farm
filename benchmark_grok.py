#!/usr/bin/env python3
"""benchmark_grok.py — tes akun Grok hasil farm: call model + reasoning + usage endpoint.

Pakai SSO cookies dari v2_sso.txt (via proxy WebShare).
1) Cek endpoint /api/models (list model yg tersedia utk akun ini)
2) Call model reasoning (grok-4.5 / grok-4 / grok-3) dgn prompt reasoning
3) Cek endpoint usage (kuota)
"""
import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, "/home/grok/grok-farm")
import farm_v2


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
    raise RuntimeError("tidak ada akun valid di v2_sso.txt")


def cookie_header(cookies):
    return "; ".join(f"{c['name']}={c['value']}" for c in cookies if c.get("name") and c.get("value"))


def http_json(url, cookies, method="GET", body=None, timeout=40):
    headers = {
        "Cookie": cookie_header(cookies),
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/148.0 Safari/537.36",
        "Accept": "application/json",
        "Origin": "https://grok.com",
        "Referer": "https://grok.com/",
    }
    if body is not None:
        headers["Content-Type"] = "application/json"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode(errors="replace")
            return resp.status, raw[:4000]
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")[:2000]
    except Exception as e:
        return -1, str(e)[:500]


def main():
    acc = load_account()
    print("AKUN:", acc.get("email"), flush=True)
    cookies = acc.get("sso_cookies", [])
    print("cookies:", len(cookies), flush=True)

    # 1) models list
    st, body = http_json("https://api.x.ai/v1/models", cookies)
    print("\n=== /v1/models ===", flush=True)
    print("status:", st, flush=True)
    print("body:", body[:1500], flush=True)

    # 2) usage endpoint (banyak variasi)
    print("\n=== USAGE ===", flush=True)
    for u in ("https://api.x.ai/v1/usage", "https://api.x.ai/v1/credits",
              "https://grok.com/rest/app-container/usage"):
        st, body = http_json(u, cookies)
        print(f"{u} -> {st}: {body[:300]}", flush=True)

    # 3) reasoning call — coba model yg mungkin
    print("\n=== REASONING CALL ===", flush=True)
    prompt = "Answer concisely. Solve: if a train travels 60 km/h for 2 hours then 90 km/h for 1.5 hours, what is the total distance? Show your reasoning step by step."
    for model in ("grok-4.5", "grok-4", "grok-3", "grok-4-fast"):
        body = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 500,
        }
        st, resp = http_json("https://api.x.ai/v1/chat/completions", cookies, method="POST", body=body)
        print(f"--- {model} -> {st} ---", flush=True)
        print(resp[:900], flush=True)
        if st == 200:
            print(">> MODEL OK:", model, flush=True)
            break
        time.sleep(1)


if __name__ == "__main__":
    main()
