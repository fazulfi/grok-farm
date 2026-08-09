#!/usr/bin/env python3
import json
with open("/home/grok/grok-farm/v2_sso.txt") as f:
    lines = [l for l in f if l.strip() and l.strip() != "null"]
accts = []
for l in lines:
    try:
        d = json.loads(l)
        if d.get("refresh_token"):
            accts.append(d)
    except Exception:
        pass
print("Total dgn RT:", len(accts))
for i, a in enumerate(accts[:15]):
    print(f"{i+1}. {a.get('email')} rt={len(a.get('refresh_token',''))}")
