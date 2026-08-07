#!/usr/bin/env python3
"""inject_grok_cli_9router.py — inject akun farm ke 9Router (grok-cli) DENGAN PROXY.

Metode SIMPEL (no browser, no device-code):
  POST /api/oauth/grok-cli/exchange {"code": "<access_token JWT>"}
    → connection grok-cli dibuat (authType: access_token)
  PUT  /api/providers/<id> {"proxyPoolId": "<pool>"}
    → assign proxy pool per connection (WAJIB — konsisten egress, anti-plenger)

Sumber pool: proxyPools 9Router (Imported <ip>:<port> = WebShare yg sama dgn farm).
Proxy rotate: tiap akun dapat pool berbeda (round-robin).

Input: v2_sso.txt (email|password|...|access_token) atau file JSONL {email, access_token}.
Run: R9_TOKEN=<cli-token> python3 inject_grok_cli_9router.py [--input v2_sso.txt] [--pool rotate|per-akun]
"""
import argparse
import json
import os
import re
import sys
import urllib.request

R9 = os.environ.get("R9_BASE", "http://127.0.0.1:20228")
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/148.0 Safari/537.36"


def req(method, path, body=None, token=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(f"{R9}{path}", data=data, method=method)
    if token:
        r.add_header("x-9r-cli-token", token)
    r.add_header("Content-Type", "application/json")
    r.add_header("User-Agent", UA)
    r.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=25) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {"error": e.reason}


def load_accounts(path):
    """Baca v2_sso.txt (JSONL) ATAU accounts.txt (email|pw|...|access|refresh)."""
    accounts = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line == "null":
                continue
            if line.startswith("{"):
                try:
                    d = json.loads(line)
                    if d.get("access_token"):
                        accounts.append({"email": d.get("email"), "token": d["access_token"]})
                except Exception:
                    continue
            elif "|" in line:
                parts = line.split("|")
                # email|pw|...|access_token di index 2 (accounts.txt farm.py)
                token = parts[2].strip() if len(parts) > 2 else ""
                if token.count(".") >= 2:
                    accounts.append({"email": parts[0].strip(), "token": token})
    return accounts


def load_pools(token):
    """Ambil semua proxy pool Imported <ip>:<port> (WebShare)."""
    st, d = req("GET", "/api/proxy-pools", token=token)
    pools = []
    if st == 200:
        raw = d.get("proxyPools") or d.get("pools") or d.get("data") or []
        for p in raw:
            data = p.get("data") or p
            if isinstance(data, str):
                try:
                    data = json.loads(data)
                except Exception:
                    continue
            if isinstance(data, dict) and data.get("name", "").startswith("Imported"):
                pools.append({"id": p["id"], "name": data.get("name")})
    return pools


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="v2_sso.txt", help="file akun (JSONL atau accounts.txt)")
    ap.add_argument("--token", default=os.environ.get("R9_TOKEN", ""), help="9router cli token")
    ap.add_argument("--pool-mode", choices=["per-akun", "rotate"], default="per-akun",
                    help="per-akun = 1 proxy unik per akun; rotate = round-robin pool")
    args = ap.parse_args()

    token = args.token
    if not token:
        print("FAIL: butuh --token / R9_TOKEN"); return 1

    accounts = load_accounts(args.input)
    if not accounts:
        print("FAIL: tidak ada akun dgn access_token di", args.input); return 1
    print(f"akun: {len(accounts)}", flush=True)

    pools = load_pools(token)
    if not pools:
        print("WARN: tidak ada proxy pool Imported di 9router — connection tanpa proxy!")
    else:
        print(f"proxy pools: {len(pools)} (WebShare)", flush=True)

    ok = fail = 0
    pool_idx = 0
    for i, acc in enumerate(accounts):
        email = acc["email"] or f"Account {i+1}"
        try:
            # 1) exchange JWT → connection
            st, res = req("POST", "/api/oauth/grok-cli/exchange",
                          {"code": acc["token"]}, token=token)
            if st != 200 or not res.get("success"):
                print(f"[{i+1}] FAIL {email}: {res}", flush=True)
                fail += 1
                continue
            conn = res.get("connection", {})
            conn_id = conn.get("id")
            print(f"[{i+1}] OK {email} -> conn {conn_id}", flush=True)

            # 2) assign proxy pool (WAJIB)
            if pools:
                pool = pools[pool_idx % len(pools)]
                pool_idx += 1
                st2, res2 = req("PUT", f"/api/providers/{conn_id}",
                                {"proxyPoolId": pool["id"]}, token=token)
                if st2 == 200:
                    print(f"     proxy -> {pool['name']}", flush=True)
                else:
                    print(f"     WARN proxy assign: {res2}", flush=True)
            ok += 1
        except Exception as e:
            print(f"[{i+1}] ERROR {email}: {e}", flush=True)
            fail += 1

    print(f"\nDONE: ok={ok} fail={fail}", flush=True)
    return 0 if fail == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
