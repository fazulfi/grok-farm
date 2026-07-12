#!/usr/bin/env python3
"""Read JSONL from stdin: {email, access_token, refresh_token, proxy}
Insert/update grok-cli + xai connections in 9router DB.
"""
import sys, json, sqlite3, uuid, base64
from datetime import datetime, timezone

DB = "/var/lib/9router/db/data.sqlite"

def now_iso():
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()

def decode_jwt(token):
    try:
        parts = token.split(".")
        payload = parts[1] + "=" * (4 - len(parts[1]) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return {}

def build_grok_cli(email, access_token, refresh_token, proxy):
    j = decode_jwt(access_token)
    now = now_iso()
    return {
        "displayName": f"{j.get('given_name','User')} {j.get('family_name','Grok')}",
        "accessToken": access_token,
        "refreshToken": refresh_token or "",
        "expiresAt": now,
        "scope": "openid profile email offline_access grok-cli:access api:access conversations:read conversations:write",
        "testStatus": "active",
        "expiresIn": 21600,
        "providerSpecificData": {
            "authMethod": "api",
            "idToken": access_token,
            "email": email,
            "userId": j.get("sub", str(uuid.uuid4())),
            "hasGrokCodeAccess": True,
            "subscriptionTier": None,
            "connectionProxyEnabled": True,
            "connectionProxyUrl": proxy,
            "connectionNoProxy": "",
        },
        "lastRefreshAt": now,
        "modelLock_grok-4.5": None,
        "errorCode": None,
        "backoffLevel": 0,
    }

def main():
    conn = sqlite3.connect(DB)
    ok = fail = 0
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            p = json.loads(line)
        except Exception as e:
            print(f"FAIL parse: {e}")
            fail += 1
            continue
        email = p.get("email")
        at = p.get("access_token") or ""
        rt = p.get("refresh_token") or ""
        proxy = p.get("proxy") or "http://cjfaxfbs-BR-CA-MX-SG-US-rotate:8oppv7sjg4tw@p.webshare.io:80"
        if not email or not at.startswith("eyJ"):
            print(f"FAIL {email}: bad token")
            fail += 1
            continue
        now = now_iso()
        try:
            grok = build_grok_cli(email, at, rt, proxy)
            xai = {
                "apiKey": at,
                "providerSpecificData": {
                    "connectionProxyEnabled": True,
                    "connectionProxyUrl": proxy,
                    "connectionNoProxy": "",
                },
            }
            # grok-cli
            ex = conn.execute("SELECT id FROM providerConnections WHERE provider='grok-cli' AND name=?", (email,)).fetchone()
            if not ex:
                conn.execute(
                    "INSERT INTO providerConnections (id, provider, authType, name, email, priority, isActive, data, createdAt, updatedAt) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (str(uuid.uuid4()), "grok-cli", "oauth", email, email, 1, 1, json.dumps(grok), now, now),
                )
            else:
                conn.execute("UPDATE providerConnections SET data=?, updatedAt=? WHERE id=?", (json.dumps(grok), now, ex[0]))
            # xai
            xname = f"Grok {email}"
            ex2 = conn.execute("SELECT id FROM providerConnections WHERE provider='xai' AND name=?", (xname,)).fetchone()
            if not ex2:
                conn.execute(
                    "INSERT INTO providerConnections (id, provider, authType, name, email, priority, isActive, data, createdAt, updatedAt) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (str(uuid.uuid4()), "xai", "apikey", xname, email, 2, 1, json.dumps(xai), now, now),
                )
            else:
                conn.execute("UPDATE providerConnections SET data=?, updatedAt=? WHERE id=?", (json.dumps(xai), now, ex2[0]))
            conn.commit()
            print(f"OK {email}")
            ok += 1
        except Exception as e:
            print(f"FAIL {email}: {e}")
            fail += 1
    conn.close()
    print(f"SUMMARY {ok} ok {fail} fail")

if __name__ == "__main__":
    main()
