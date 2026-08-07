#!/usr/bin/env python3
"""refresh_9router.py — auto-refresh token akun grok-farm + update connection 9router.

Akun akses token expires 6 jam. Tanpa refresh, connection mati & perlu re-farm.
Alur per akun (dari v2_sso.txt):
  1. POST auth.x.ai/oauth2/token grant_type=refresh_token → access_token BARU + refresh_token BARU
  2. Simpan refresh_token baru ke v2_sso (rotasi)
  3. Update connection 9router (PUT providerSpecificData.idToken = access_token baru)

Run:
  R9_TOKEN=<cli-token> python3 refresh_9router.py [--v2 v2_sso.txt] [--input accounts.json (optional -force)]
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.request

XAI_TOKEN = "https://auth.x.ai/oauth2/token"
XAI_CLIENT_ID = "b1a00492-073a-47ea-816f-4c329264a828"
SCOPE = "openid profile email offline_access grok-cli:access api:access conversations:read conversations:write"
R9 = os.environ.get("R9_BASE", "http://127.0.0.1:20228")
R9_TOKEN = os.environ.get("R9_TOKEN", "")


def _load_r9_token_from_env_file():
    """Fallback: baca R9_TOKEN dari .env (V2_R9_TOKEN) kalau env kosong."""
    if R9_TOKEN:
        return R9_TOKEN
    try:
        for p in (".env", "/home/grok/grok-farm/.env", "/home/gamesim/grok-farm/.env"):
            try:
                for line in open(p):
                    line = line.strip()
                    if line.startswith("V2_R9_TOKEN="):
                        return line.split("=", 1)[1].strip()
            except FileNotFoundError:
                continue
    except Exception:
        pass
    return R9_TOKEN
UA = "grok-shell/0.2.99 (linux; x86_64)"


def refresh_token(rt):
    """Refresh access token via x.ai OAuth. Return dict (access_token, refresh_token, expires_in) or None."""
    body = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "client_id": XAI_CLIENT_ID,
        "refresh_token": rt,
        "scope": SCOPE,
        "referrer": "grok-build",
    }).encode()
    req = urllib.request.Request(XAI_TOKEN, data=body, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    req.add_header("User-Agent", UA)
    req.add_header("x-grok-client-version", "0.2.99")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return {"error": e.code, "detail": e.read().decode()[:200]}
    except Exception as e:
        return {"error": str(e)}


def r9_req(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{R9}{path}", data=data, method=method)
    req.add_header("x-9r-cli-token", R9_TOKEN)
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", "Mozilla/5.0 (X11; Linux x86_64) Chrome/148.0 Safari/537.36")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {"error": e.reason}
    except Exception as e:
        return -1, {"error": str(e)}


def get_9router_conn_by_email():
    """Map email → connection id utk provider grok-cli.
    Connection inject (exchange) bernama 'Account N' tanpa email — mapping
    by ORDER: urutan connection grok-cli (kecuali manual) == urutan akun v2_sso.
    """
    st, d = r9_req("GET", "/api/providers")
    if st != 200:
        return {}
    m = {}
    conns = [c for c in d.get("connections", []) if c.get("provider") == "grok-cli" and c.get("isActive")]
    # urutkan by createdAt utk stabilitas
    conns.sort(key=lambda c: c.get("createdAt") or "")
    auto_idx = 0
    for c in conns:
        key = (c.get("email") or c.get("name") or "").lower()
        if key and "@" in key:
            m[key] = c["id"]
        elif c.get("name", "").startswith("Account"):
            # connection auto-inject: Account N → urut
            m[f"__auto_{auto_idx}"] = c["id"]
            auto_idx += 1
    return m, conns


def load_accounts(path):
    accts = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line == "null":
                continue
            if line.startswith("{"):
                try:
                    d = json.loads(line)
                    if d.get("refresh_token"):
                        accts.append(d)
                except Exception:
                    continue
    return accts


def update_v2_line(path, email, new_data):
    """Rewrite baris akun dgn refresh_token baru (rotasi) + token expires_at."""
    out = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                if (d.get("email") == email) and d.get("refresh_token"):
                    d["refresh_token"] = new_data.get("refresh_token", d["refresh_token"])
                    d["access_token"] = new_data.get("access_token", d.get("access_token"))
                    d["last_refresh"] = time.time()
                out.append(json.dumps(d))
            except Exception:
                out.append(line)
    with open(path, "w") as f:
        f.write("\n".join(out) + "\n")


def main():
    global R9_TOKEN
    ap = argparse.ArgumentParser()
    ap.add_argument("--v2", default="v2_sso.txt", help="file akun (JSONL)")
    args = ap.parse_args()
    R9_TOKEN = _load_r9_token_from_env_file()
    if not R9_TOKEN:
        print("FAIL: butuh R9_TOKEN"); return 1

    accts = load_accounts(args.v2)
    if not accts:
        print(f"tidak ada akun dgn refresh_token di {args.v2}")
        return 0
    print(f"akun dgn refresh_token: {len(accts)}", flush=True)

    # map email → connection
    conn_map, all_conns = get_9router_conn_by_email()
    print(f"connection grok-cli: {len(all_conns)}", flush=True)

    # auto-connections (Account N) diurutkan — kita map ke akun v2_sso by index
    auto_ids = [conn_map[f"__auto_{i}"] for i in range(len(all_conns)) if f"__auto_{i}" in conn_map]

    ok = failskipped = 0
    # akun yg sudah inject (urutan di v2_sso == urutan Account N inject)
    injected_idx = 0
    for i, acct in enumerate(accts):
        email = acct["email"]
        rt = acct["refresh_token"]
        try:
            res = refresh_token(rt)
            if res.get("error"):
                print(f"[{i+1}] SKIP {email}: refresh err {res.get('error')}", flush=True)
                failskipped += 1
                continue
            new_at = res.get("access_token", "")
            new_rt = res.get("refresh_token", rt)
            print(f"[{i+1}] OK {email}: new_at={len(new_at)}ch new_rt={len(new_rt)}ch", flush=True)

            # update connection 9router dgn access_token baru
            conn_id = conn_map.get(email.lower())
            if not conn_id and injected_idx < len(auto_ids):
                conn_id = auto_ids[injected_idx]
            injected_idx += 1  # maju utk akun berikutnya (matching by order)
            if conn_id:
                st, _ = r9_req("PUT", f"/api/providers/{conn_id}",
                               {"providerSpecificData": {"idToken": new_at}})
                print(f"     conn {conn_id[:8]}: PUT idToken -> {st}", flush=True)
            else:
                st, exch = r9_req("POST", "/api/oauth/grok-cli/exchange", {"code": new_at})
                if st == 200 and exch.get("success"):
                    print(f"     re-inject via exchange -> conn", flush=True)
                else:
                    print(f"     WARN: tidak ada conn & exchange gagal: {exch}", flush=True)

            update_v2_line(args.v2, email, {"access_token": new_at, "refresh_token": new_rt})
            ok += 1
        except Exception as e:
            print(f"[{i+1}] ERROR {email}: {e}", flush=True)
            failskipped += 1
        time.sleep(1)

    print(f"\nDONE: refreshed={ok} skipped/failed={failskipped}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
