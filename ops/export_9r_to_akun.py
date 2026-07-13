#!/usr/bin/env python3
"""Export active grok-cli connections from 9router SQLite into farm akun.db schema.

Run ON the 9router gateway host (needs read access to data.sqlite).

  python3 export_9r_to_akun.py /tmp/akun_rebuild.db

Does NOT print tokens. Safe for operator logs (counts only).
Copy output DB to farm VPS as ~/grok-farm/akun.db (chmod 600).

See docs/MIGRATION.md §4 path B (no farm backup).
"""
from __future__ import annotations

import base64
import json
import sqlite3
import sys
import time
from pathlib import Path

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/akun_rebuild.db")
DB = Path(sys.argv[2] if len(sys.argv) > 2 else "/var/lib/9router/db/data.sqlite")

DDL = """
CREATE TABLE IF NOT EXISTS accounts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  email TEXT UNIQUE NOT NULL,
  password TEXT,
  access_token TEXT,
  refresh_token TEXT,
  created_at TEXT,
  batch_id TEXT,
  proxy_used TEXT,
  status TEXT DEFAULT 'injected',
  injected_at TEXT,
  grok_cli_connection_id TEXT,
  ninerouter_name TEXT,
  notes TEXT,
  token_exp INTEGER,
  token_health TEXT,
  last_probe_at TEXT,
  last_probe_status TEXT,
  last_probe_http INTEGER,
  needs_relogin INTEGER DEFAULT 0
);
"""


def jwt_exp(token: str):
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return None
        pad = "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(parts[1] + pad))
        return payload.get("exp")
    except Exception:
        return None


def health(exp):
    if not exp:
        return "invalid"
    now = int(time.time())
    if exp <= now:
        return "expired"
    if exp <= now + 7 * 86400:
        return "expiring_soon"
    return "ok"


def main() -> int:
    if not DB.is_file():
        print(f"ERR missing 9router db: {DB}", file=sys.stderr)
        return 2

    con = sqlite3.connect(str(DB))
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT id, provider, name, email, isActive, data, createdAt, updatedAt "
        "FROM providerConnections WHERE provider='grok-cli' AND isActive=1"
    ).fetchall()

    out = sqlite3.connect(str(OUT))
    out.execute(DDL)
    inserted = 0
    skipped = 0
    for r in rows:
        data = {}
        try:
            data = json.loads(r["data"] or "{}")
        except Exception:
            skipped += 1
            continue
        email = (r["email"] or data.get("email") or r["name"] or "").strip().lower()
        if "@" not in email:
            n = (r["name"] or "").strip()
            if "@" in n:
                email = n.lower()
            else:
                skipped += 1
                continue
        at = data.get("accessToken") or data.get("access_token") or ""
        rt = data.get("refreshToken") or data.get("refresh_token") or ""
        if not at:
            skipped += 1
            continue
        exp = jwt_exp(at)
        th = health(exp)
        out.execute(
            """
            INSERT INTO accounts(
              email, password, access_token, refresh_token, created_at, batch_id,
              proxy_used, status, injected_at, grok_cli_connection_id, ninerouter_name,
              notes, token_exp, token_health, needs_relogin
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)
            ON CONFLICT(email) DO UPDATE SET
              access_token=excluded.access_token,
              refresh_token=excluded.refresh_token,
              status='injected',
              grok_cli_connection_id=excluded.grok_cli_connection_id,
              ninerouter_name=excluded.ninerouter_name,
              injected_at=excluded.injected_at,
              token_exp=excluded.token_exp,
              token_health=excluded.token_health
            """,
            (
                email,
                data.get("password") or "",
                at,
                rt,
                r["createdAt"],
                "rebuild_from_9router",
                "",
                "injected",
                r["updatedAt"] or r["createdAt"],
                r["id"],
                r["name"] or email,
                "restored_from_9router",
                exp,
                th,
            ),
        )
        inserted += 1
    out.commit()
    counts = list(out.execute("SELECT status, count(*) c FROM accounts GROUP BY status"))
    health_c = list(
        out.execute("SELECT token_health, count(*) c FROM accounts GROUP BY token_health")
    )
    total = out.execute("SELECT count(*) FROM accounts").fetchone()[0]
    print("OUT", str(OUT))
    print("INSERTED", inserted, "SKIPPED", skipped)
    print("BY_STATUS", counts)
    print("BY_HEALTH", health_c)
    print("TOTAL", total)
    out.close()
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
