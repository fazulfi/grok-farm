#!/usr/bin/env python3
"""Import results/batch_*/accounts.txt into akun.db."""
from __future__ import annotations

import glob
import os
import sqlite3
import sys

from db_schema import DEFAULT_DB, harden_db_file, migrate
from token_util import jwt_exp_unix, token_health, update_account_token_meta

DB = os.path.expanduser(os.environ.get("GROK_AKUN_DB") or DEFAULT_DB)


def main() -> int:
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    migrate(conn)

    imported = 0
    for bdir in sorted(glob.glob(os.path.expanduser("~/grok-farm/results/batch_*"))):
        acct_file = os.path.join(bdir, "accounts.txt")
        if not os.path.exists(acct_file):
            continue
        batch_id = os.path.basename(bdir)
        for line in open(acct_file, encoding="utf-8", errors="replace"):
            parts = line.strip().split("|")
            if len(parts) < 1 or not parts[0]:
                continue
            email = parts[0]
            password = parts[1] if len(parts) > 1 else ""
            at = parts[2] if len(parts) > 2 else ""
            rt = parts[3] if len(parts) > 3 else ""
            exp = jwt_exp_unix(at)
            health = token_health(at)
            # Fail-closed: dead tokens never enter inject queue as farmed
            if not at or not str(at).startswith("eyJ") or health in ("expired", "invalid", "missing"):
                status = "error"
                notes = "token_expired" if health == "expired" else "bad_token"
            else:
                status = "farmed"
                notes = None
            try:
                conn.execute(
                    """INSERT INTO accounts
                       (email,password,access_token,refresh_token,batch_id,status,token_exp,token_health,notes)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (email, password, at, rt, batch_id, status, exp, health, notes),
                )
                imported += 1
            except sqlite3.IntegrityError:
                # refresh token meta for existing farmed rows if empty
                row = conn.execute(
                    "SELECT access_token, token_exp, status FROM accounts WHERE email=?",
                    (email,),
                ).fetchone()
                if row is not None and at and (not row["token_exp"]):
                    update_account_token_meta(conn, email, at)
                if (
                    row is not None
                    and row["status"] == "farmed"
                    and health in ("expired", "invalid", "missing")
                ):
                    note = "token_expired" if health == "expired" else "bad_token"
                    conn.execute(
                        "UPDATE accounts SET status='error', notes=?, token_exp=?, token_health=? "
                        "WHERE email=? AND status='farmed'",
                        (note, exp, health, email),
                    )

    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
    conn.close()
    harden_db_file(DB)
    if imported > 0:
        print(f"[DB] Imported {imported} new accounts | Total: {total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
