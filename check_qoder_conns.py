#!/usr/bin/env python3
"""check_qoder_conns.py — cek existing qoder connections di 9router (device flow).
"""
import hashlib, json, urllib.request

def r9_token():
    mid = open('/var/lib/9router-ind/machine-id').read().strip()
    sec = open('/var/lib/9router-ind/auth/cli-secret').read().strip()
    return hashlib.sha256((mid + '9r-cli-auth' + sec).encode()).hexdigest()[:16]

tok = r9_token()
req = urllib.request.Request("http://127.0.0.1:20228/api/providers", headers={"x-9r-cli-token": tok})
try:
    d = json.loads(urllib.request.urlopen(req, timeout=15).read().decode())
except Exception as e:
    print("err:", e); exit()
# filter qoder, redact token value
provs = d if isinstance(d, list) else d.get("providerConnections") or d.get("connections") or d.get("data") or []
print("total connections:", len(provs) if isinstance(provs, list) else "?")
qoder = [c for c in provs if (c.get("provider") or "").lower() == "qoder"] if isinstance(provs, list) else []
print("qoder conns:", len(qoder))
for c in qoder:
    psd = c.get("providerSpecificData") or {}
    print(" id:", c.get("id","")[:8], "| name:", c.get("name"), "| email:", c.get("email"), "| auth:", c.get("authType"))
    print("   has accessToken:", bool(psd.get("accessToken")), "| keys:", list(psd.keys())[:8])
