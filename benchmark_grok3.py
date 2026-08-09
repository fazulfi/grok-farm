#!/usr/bin/env python3
"""benchmark_grok3.py — tes akun farm PAKAI access_token (yang diambil saat farming).

Call: models + reasoning + usage.
"""
import json
import sys
import urllib.request

def load_account(path="/home/grok/grok-farm/v2_sso.txt"):
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line == "null":
                continue
            try:
                d = json.loads(line)
                if d and d.get("access_token"):
                    return d
            except Exception:
                continue
    raise RuntimeError("tidak ada akun dgn access_token")


def api(url, token, method="GET", body=None):
    h = {"Authorization": f"Bearer {token}", "User-Agent": "Mozilla/5.0", "Accept": "application/json"}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.read().decode(errors="replace")[:8000]
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")[:4000]
    except Exception as e:
        return -1, str(e)[:500]


def main():
    acc = load_account()
    at = acc["access_token"]
    print("AKUN:", acc["email"], flush=True)
    print("TOKEN prefix:", at[:40], "len:", len(at), flush=True)

    # 1) models
    st, body = api("https://api.x.ai/v1/models", at)
    print("\n=== /v1/models:", st, "===", flush=True)
    if st == 200:
        try:
            ids = [m.get("id") for m in json.loads(body).get("data", [])]
            print("models:", json.dumps(ids, indent=0)[:3000], flush=True)
        except Exception as e:
            print("parse err", e, body[:500], flush=True)
    else:
        print(body[:600], flush=True)

    # 2) reasoning
    print("\n=== REASONING ===", flush=True)
    prompt = "Answer concisely. A train travels 60 km/h for 2 hours, then 90 km/h for 1.5 hours. What is the total distance? Show your reasoning step by step."
    for model in ("grok-4.5", "grok-4", "grok-3", "grok-4-fast", "grok-4.1", "grok-4-fast-1"):
        st, body = api("https://api.x.ai/v1/chat/completions", at, method="POST",
                       body={"model": model, "messages": [{"role": "user", "content": prompt}], "max_tokens": 700})
        print(f"--- {model} -> {st} ---", flush=True)
        try:
            d = json.loads(body)
            if st == 200:
                msg = d.get("choices", [{}])[0].get("message", {})
                print("content:", (msg.get("content") or "")[:700], flush=True)
                print("reasoning:", (msg.get("reasoning") or "")[:300], flush=True)
                print(">> OK:", model, flush=True)
                break
            else:
                print("err:", json.dumps(d)[:300], flush=True)
        except Exception as e:
            print("raw:", body[:400], flush=True)
        if st == 200:
            break
        sys.stdout.flush()

    # 3) usage - beberapa endpoint
    print("\n=== USAGE ===", flush=True)
    for u in ("https://api.x.ai/v1/usage", "https://api.x.ai/v1/credits", "https://api.x.ai/v1/user/usage",
              "https://api.x.ai/usage"):
        st, body = api(u, at)
        print(f"{u} -> {st}: {body[:300]}", flush=True)


if __name__ == "__main__":
    main()
