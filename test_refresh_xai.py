#!/usr/bin/env python3
"""test_refresh_xai.py — tes refresh token x.ai (grant_type=refresh_token)."""
import json, sys, urllib.request, urllib.parse

def get_acct(path="/home/grok/grok-farm/v2_sso.txt"):
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line and line.startswith("{"):
                try:
                    d = json.loads(line)
                    if d.get("refresh_token"):
                        return d
                except Exception:
                    pass
    return None

acct = get_acct()
if not acct:
    print("NO ACCT dgn refresh_token"); sys.exit(1)
print("EMAIL:", acct["email"], flush=True)
print("RT len:", len(acct["refresh_token"]), flush=True)

client_id = "b1a00492-073a-47ea-816f-4c329264a828"
body = urllib.parse.urlencode({
    "grant_type": "refresh_token",
    "client_id": client_id,
    "refresh_token": acct["refresh_token"],
    "scope": "openid profile email offline_access grok-cli:access api:access conversations:read conversations:write",
    "referrer": "grok-build",
}).encode()
req = urllib.request.Request("https://auth.x.ai/oauth2/token", data=body, method="POST")
req.add_header("Content-Type", "application/x-www-form-urlencoded")
req.add_header("User-Agent", "grok-shell/0.2.99 (linux; x86_64)")
req.add_header("x-grok-client-version", "0.2.99")
try:
    with urllib.request.urlopen(req, timeout=20) as r:
        d = json.loads(r.read().decode())
        print("STATUS: 200")
        print("NEW_ACCESS_LEN:", len(d.get("access_token","")))
        print("NEW_REFRESH:", len(d.get("refresh_token","")))
        print("EXPIRES_IN:", d.get("expires_in"))
        print("ID_TOKEN:", len(d.get("id_token","")))
except urllib.error.HTTPError as e:
    print("HTTP", e.code, e.read().decode()[:400])
