#!/usr/bin/env python3
"""fix_email_rules.py — perbaiki rule email routing: hapus drop, set forward catch-all."""
import json, os, sys, urllib.request, urllib.error

TOK = os.environ.get("CF_TOKEN", os.environ.get("CLOUDFLARE_API_TOKEN", ""))
API = "https://api.cloudflare.com/client/v4"
DEST = "your-gmail@gmail.com"

def req(m, p, b=None):
    data = json.dumps(b).encode() if b is not None else None
    r = urllib.request.Request(f"{API}{p}", data=data, method=m)
    r.add_header("Authorization", f"Bearer {TOK}")
    r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=20) as x:
            return json.loads(x.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode() or "{}")
        except Exception:
            return {"success": False, "errors": [{"code": e.code, "msg": e.reason}]}

def main():
    # semua zona di akun
    zones = req("GET", "/zones?per_page=50").get("result", [])
    print("zona:", [z["name"] for z in zones])
    for z in zones:
        name, zid = z["name"], z["id"]
        print(f"\n=== {name} ===")
        # enable email routing
        print(" enable:", req("POST", f"/zones/{zid}/email/routing/enable").get("success"))
        # rules: hapus semua, tambah forward
        rules = req("GET", f"/zones/{zid}/email/routing/rules").get("result", [])
        for r in rules:
            d = req("DELETE", f"/zones/{zid}/email/routing/rules/{r['id']}")
            print(f" del {r['id'][:8]}:", d.get("success"))
        add = req("POST", f"/zones/{zid}/email/routing/rules",
                  {"matchers": [{"type": "all"}],
                   "actions": [{"type": "forward", "value": [DEST]}]})
        print(" add forward:", add.get("success"), (add.get("errors") or [])[:1])
        # verifikasi
        chk = req("GET", f"/zones/{zid}/email/routing/rules").get("result", [])
        print(" rules sekarang:", [(r.get("matchers"), r.get("actions")) for r in chk])
    print("\nDONE")

if __name__ == "__main__":
    main()
