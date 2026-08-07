#!/usr/bin/env python3
"""setup_email_routing.py — aktifkan CF Email Routing + set catch-all → Gmail.

Pakai token domain CF (token_domain). Otomasi buat farm Grok:
- aktifkan email routing per zona
- set rule `all → forward` ke destination Gmail (catch-all)
- update MX sesuai CF (tambahkan kalau belum)
Param: --dest email → semua zona di akun; atau --zone <nama> utk 1 zona.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request

TOKEN = os.environ.get("CF_TOKEN", os.environ.get("CLOUDFLARE_API_TOKEN", ""))
API = "https://api.cloudflare.com/client/v4"
DEST = ""  # isi via --dest

def req(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(f"{API}{path}", data=data, method=method)
    r.add_header("Authorization", f"Bearer {TOKEN}")
    r.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(r, timeout=20) as resp:
        return json.loads(resp.read().decode())

def zones():
    d = req("GET", "/zones?per_page=50")
    return [(z["name"], z["id"]) for z in d.get("result", [])]

def main():
    global DEST
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--dest", required=True, help="destination gmail (verified di CF)")
    ap.add_argument("--zone", help="hanya 1 zona (nama). default: semua")
    args = ap.parse_args()
    DEST = args.dest

    zlist = zones()
    if args.zone:
        zlist = [z for z in zlist if z[0] == args.zone]
    if not zlist:
        print("Tidak ada zona ditemukan"); return 1

    for name, zid in zlist:
        print(f"\n=== {name} ({zid}) ===")
        # 1. enable email routing
        try:
            d = req("POST", f"/zones/{zid}/email/routing/enable")
            print(" enable:", d.get("success"), (d.get("errors") or [])[:1])
        except Exception as e:
            print(" enable-err:", e)
        # 2. update MX records (CF email routing membutuhkan MX + TXT spf)
        mx_mismatch = False
        d = req("GET", f"/zones/{zid}/dns_records?type=MX")
        mxs = d.get("result", [])
        cf_mx = [f"route{n}.mx.cloudflare.net" for n in (1,2)]
        have = {r.get("content") for r in mxs}
        if not {"route1.mx.cloudflare.net","route2.mx.cloudflare.net"}.issubset(have):
            mx_mismatch = True
            # hapus MX non-CF (route existing yang bukan CF) lalu tambah
            for r in mxs:
                if r.get("content") not in cf_mx:
                    req("DELETE", f"/zones/{zid}/dns_records/{r['id']}")
                    print(" del mx:", r.get("content"))
            for n in (1,2):
                # cek sudah ada
                if f"route{n}.mx.cloudflare.net" not in have:
                    req("POST", f"/zones/{zid}/dns_records",
                        {"type":"MX","name":name,"content":f"route{n}.mx.cloudflare.net","priority":10 if n==1 else 20})
                    print(f" add mx route{n}")
        else:
            print(" mx: sudah CF")
        # 3. set catch-all rule: ganti existing all/drop dgn all/forward
        rules = req("GET", f"/zones/{zid}/email/routing/rules").get("result", [])
        all_rule = next((r for r in rules if any(m.get("type")=="all" for m in r.get("matchers",[]))), None)
        if all_rule:
            rid = all_rule["id"]
            req("PUT", f"/zones/{zid}/email/routing/rules/{rid}",
                {"matchers":[{"type":"all"}],"actions":[{"type":"forward","value":[DEST]}]})
            print(" update rule all->forward:", DEST)
        else:
            req("POST", f"/zones/{zid}/email/routing/rules",
                {"matchers":[{"type":"all"}],"actions":[{"type":"forward","value":[DEST]}]})
            print(" add rule all->forward:", DEST)
    print("\nDONE")
    return 0

if __name__ == "__main__":
    sys.exit(main())
