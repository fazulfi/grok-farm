#!/usr/bin/env python3
"""Unified workflow: import DB + inject farmed accounts using proxies from 9router proxyPools only."""
import sqlite3, json, os, glob, subprocess, random
from datetime import datetime, timezone

CSA_DB = os.path.expanduser("~/grok-farm/akun.db")
RESULTS = os.path.expanduser("~/grok-farm/results")
SSH = ["ssh", "-i", os.path.expanduser("~/.ssh/id_ed25519"), "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=15", "root@49.12.82.34", "-p", "39999"]

def now_iso():
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()

def import_batches():
    conn = sqlite3.connect(CSA_DB)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            access_token TEXT,
            refresh_token TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            batch_id TEXT,
            proxy_used TEXT,
            status TEXT DEFAULT 'farmed',
            injected_at TIMESTAMP,
            grok_cli_connection_id TEXT,
            ninerouter_name TEXT,
            notes TEXT
        );
    """)
    n = 0
    for bdir in sorted(glob.glob(os.path.join(RESULTS, "batch_*"))):
        f = os.path.join(bdir, "accounts.txt")
        if not os.path.exists(f):
            continue
        batch = os.path.basename(bdir)
        for line in open(f):
            p = line.strip().split("|")
            if len(p) < 1 or not p[0]:
                continue
            try:
                conn.execute(
                    "INSERT INTO accounts (email,password,access_token,refresh_token,batch_id,status) VALUES (?,?,?,?,?,?)",
                    (p[0], p[1] if len(p)>1 else "", p[2] if len(p)>2 else "", p[3] if len(p)>3 else "", batch, "farmed"),
                )
                n += 1
            except sqlite3.IntegrityError:
                pass
    conn.commit(); conn.close()
    if n:
        print(f"[WORKFLOW] Imported {n} new accounts")
    return n

def fetch_all_proxies_from_9router():
    """Get ALL proxy URLs from 9router proxyPools via /root/list_proxies.py (source of truth)."""
    r = subprocess.run(
        SSH + ["python3", "/root/list_proxies.py"],
        capture_output=True, text=True, timeout=30,
    )
    urls = []
    for line in (r.stdout or "").splitlines():
        line = line.strip()
        if line.startswith("http"):
            urls.append(line.rstrip("/"))
    seen=set(); out=[]
    for u in urls:
        if u not in seen:
            seen.add(u); out.append(u)
    return out

def main():
    print(f"[WORKFLOW] Started {now_iso()}")
    import_batches()

    proxies = fetch_all_proxies_from_9router()
    if not proxies:
        print("[WORKFLOW] FATAL: 9router proxyPools empty — abort inject (no local proxy file fallback)")
        return 1
    print(f"[WORKFLOW] Loaded {len(proxies)} proxies from 9router proxyPools")

    conn = sqlite3.connect(CSA_DB)
    rows = conn.execute("SELECT email, access_token, refresh_token FROM accounts WHERE status='farmed' ORDER BY id").fetchall()
    conn.close()
    if not rows:
        print("[WORKFLOW] No pending farmed accounts")
        return 0
    print(f"[WORKFLOW] Pending: {len(rows)}")

    lines = []
    email_proxy = {}
    for email, at, rt in rows:
        if not at or not str(at).startswith("eyJ"):
            print(f"  skip {email}: bad token")
            continue
        proxy = random.choice(proxies)  # random from 9router pools per account
        email_proxy[email] = proxy
        lines.append(json.dumps({
            "email": email,
            "access_token": at,
            "refresh_token": rt or "",
            "proxy": proxy,
        }))
        print(f"  assign {email} -> {proxy[:55]}...")

    if not lines:
        print("[WORKFLOW] Nothing to inject")
        return 0

    data = "\n".join(lines) + "\n"
    try:
        r = subprocess.run(
            SSH + ["python3", "/root/grok_cli_bulk_inject.py"],
            input=data, capture_output=True, text=True, timeout=600,
        )
        print(r.stdout)
        if r.stderr:
            print(r.stderr[:300])
        ok_emails = set()
        for line in (r.stdout or "").splitlines():
            if line.startswith("OK "):
                ok_emails.add(line[3:].strip())
        c2 = sqlite3.connect(CSA_DB)
        for email in ok_emails:
            c2.execute(
                "UPDATE accounts SET status='injected', injected_at=?, proxy_used=? WHERE email=?",
                (now_iso(), email_proxy.get(email, ""), email),
            )
        c2.commit(); c2.close()
        print(f"[WORKFLOW] Marked {len(ok_emails)} as injected (proxy from 9router pools)")
    except Exception as e:
        print(f"[WORKFLOW] inject error: {e}")
        return 1
    print(f"[WORKFLOW] Finished {now_iso()}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
