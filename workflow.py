#!/usr/bin/env python3
"""Unified workflow: import DB + inject farmed accounts using proxies from 9router proxyPools only."""
from __future__ import annotations

import json
import os
import random
import subprocess
import sys
from datetime import datetime, timezone

from alerts import send_alert
from db_schema import DEFAULT_DB, harden_db_file, migrate, record_proxy_result
from log_redact import redact, redact_proxy_url, safe_print
from token_util import token_health, update_account_token_meta

CSA_DB = os.path.expanduser(os.environ.get("GROK_AKUN_DB") or DEFAULT_DB)
RESULTS = os.path.expanduser(os.environ.get("GROK_RESULTS_DIR") or "~/grok-farm/results")
SSH = [
    "ssh",
    "-i",
    os.path.expanduser(os.environ.get("GROK_9R_SSH_KEY") or "~/.ssh/id_ed25519"),
    "-o",
    "StrictHostKeyChecking=no",
    "-o",
    "ConnectTimeout=15",
    os.environ.get("GROK_9R_SSH") or "root@49.12.82.34",
    "-p",
    os.environ.get("GROK_9R_SSH_PORT") or "39999",
]


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


def import_batches() -> int:
    import glob
    import sqlite3

    conn = sqlite3.connect(CSA_DB)
    migrate(conn)
    n = 0
    for bdir in sorted(glob.glob(os.path.join(RESULTS, "batch_*"))):
        f = os.path.join(bdir, "accounts.txt")
        if not os.path.exists(f):
            continue
        batch = os.path.basename(bdir)
        for line in open(f, encoding="utf-8", errors="replace"):
            p = line.strip().split("|")
            if len(p) < 1 or not p[0]:
                continue
            at = p[2] if len(p) > 2 else ""
            try:
                from token_util import jwt_exp_unix

                exp = jwt_exp_unix(at)
                health = token_health(at)
                # Fail-closed: dead tokens never enter inject queue as farmed
                if (
                    not at
                    or not str(at).startswith("eyJ")
                    or health in ("expired", "invalid", "missing")
                ):
                    status = "error"
                    notes = "token_expired" if health == "expired" else "bad_token"
                else:
                    status = "farmed"
                    notes = None
                conn.execute(
                    """INSERT INTO accounts
                       (email,password,access_token,refresh_token,batch_id,status,token_exp,token_health,notes)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (
                        p[0],
                        p[1] if len(p) > 1 else "",
                        at,
                        p[3] if len(p) > 3 else "",
                        batch,
                        status,
                        exp,
                        health,
                        notes,
                    ),
                )
                n += 1
            except sqlite3.IntegrityError:
                pass
    conn.commit()
    conn.close()
    harden_db_file(CSA_DB)
    if n:
        print(f"[WORKFLOW] Imported {n} new accounts")
    return n


def fetch_all_proxies_from_9router() -> list[str]:
    """Get ALL proxy URLs from 9router proxyPools via /root/list_proxies.py (source of truth)."""
    r = subprocess.run(
        SSH + ["python3", "/root/list_proxies.py"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    urls: list[str] = []
    for line in (r.stdout or "").splitlines():
        line = line.strip()
        if line.startswith("http"):
            urls.append(line.rstrip("/"))
    seen: set[str] = set()
    out: list[str] = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def main() -> int:
    import sqlite3

    print(f"[WORKFLOW] Started {now_iso()}")
    import_batches()

    proxies = fetch_all_proxies_from_9router()
    if not proxies:
        msg = "9router proxyPools empty — abort inject (no local proxy file fallback)"
        print(f"[WORKFLOW] FATAL: {msg}")
        send_alert("inject aborted", msg, level="critical")
        return 1
    print(f"[WORKFLOW] Loaded {len(proxies)} proxies from 9router proxyPools")

    conn = sqlite3.connect(CSA_DB)
    conn.row_factory = sqlite3.Row
    migrate(conn)
    rows = conn.execute(
        "SELECT email, access_token, refresh_token FROM accounts WHERE status='farmed' ORDER BY id"
    ).fetchall()
    if not rows:
        conn.close()
        print("[WORKFLOW] No pending farmed accounts")
        return 0
    print(f"[WORKFLOW] Pending: {len(rows)}")

    lines: list[str] = []
    email_proxy: dict[str, str] = {}
    skipped_bad = 0
    for row in rows:
        email = row["email"] if isinstance(row, sqlite3.Row) else row[0]
        at = row["access_token"] if isinstance(row, sqlite3.Row) else row[1]
        rt = row["refresh_token"] if isinstance(row, sqlite3.Row) else row[2]
        if not at or not str(at).startswith("eyJ"):
            print(f"  skip {email}: bad token")
            # leave as farmed? mark error so we do not loop forever
            try:
                conn.execute(
                    "UPDATE accounts SET status='error', notes=? WHERE email=? AND status='farmed'",
                    ("bad_token", email),
                )
                conn.commit()
            except Exception:
                pass
            skipped_bad += 1
            continue
        health = token_health(at)
        if health == "expired":
            print(f"  skip {email}: token expired")
            try:
                update_account_token_meta(conn, email, at)
                conn.execute(
                    "UPDATE accounts SET status='error', notes=? WHERE email=? AND status='farmed'",
                    ("token_expired", email),
                )
                conn.commit()
            except Exception as e:
                safe_print(f"[WORKFLOW] mark expired failed {email}: {e}")
            skipped_bad += 1
            continue
        proxy = random.choice(proxies)
        email_proxy[email] = proxy
        lines.append(
            json.dumps(
                {
                    "email": email,
                    "access_token": at,
                    "refresh_token": rt or "",
                    "proxy": proxy,
                }
            )
        )
        print(f"  assign {email} -> {redact_proxy_url(proxy)} (token={health})")

    conn.close()

    if not lines:
        print("[WORKFLOW] Nothing to inject")
        if skipped_bad:
            send_alert("inject empty", f"skipped_bad={skipped_bad}", level="warning")
        return 0

    data = "\n".join(lines) + "\n"
    try:
        r = subprocess.run(
            SSH + ["python3", "/root/grok_cli_bulk_inject.py"],
            input=data,
            capture_output=True,
            text=True,
            timeout=600,
        )
        # stdout may contain emails only (OK lines) — safe; redact any accidental token dumps
        if r.stdout:
            print(redact(r.stdout))
        if r.stderr:
            print(redact(r.stderr[:500]))
        if r.returncode != 0 and not (r.stdout or "").strip():
            raise RuntimeError(
                f"inject remote exit={r.returncode} stderr={redact((r.stderr or '')[:300])}"
            )
        ok_emails: set[str] = set()
        for line in (r.stdout or "").splitlines():
            if line.startswith("OK "):
                ok_emails.add(line[3:].strip())
        attempted = set(email_proxy.keys())
        failed = attempted - ok_emails
        ts = now_iso()
        c2 = sqlite3.connect(CSA_DB)
        c2.row_factory = sqlite3.Row
        migrate(c2)
        # 1) Commit inject marks first — must not roll back if proxy_stats fails
        for email in ok_emails:
            proxy = email_proxy.get(email, "")
            c2.execute(
                "UPDATE accounts SET status='injected', injected_at=?, proxy_used=? "
                "WHERE email=? AND status='farmed'",
                (ts, proxy, email),
            )
            row = c2.execute(
                "SELECT access_token FROM accounts WHERE email=?", (email,)
            ).fetchone()
            tok = row["access_token"] if row is not None else None
            if tok:
                update_account_token_meta(c2, email, tok)
        c2.commit()
        # 2) Best-effort proxy scoring (never undo inject marks)
        try:
            for email in ok_emails:
                proxy = email_proxy.get(email, "")
                record_proxy_result(c2, proxy, True, email=email, when_iso=ts)
            for email in failed:
                proxy = email_proxy.get(email, "")
                record_proxy_result(c2, proxy, False, email=email, when_iso=ts)
            c2.commit()
        except Exception as pe:
            safe_print(f"[WORKFLOW] proxy_stats update error (inject marks kept): {pe}")
        c2.close()
        harden_db_file(CSA_DB)
        print(
            f"[WORKFLOW] Marked {len(ok_emails)} as injected "
            f"(failed={len(failed)}, proxy from 9router pools)"
        )
        if failed and not ok_emails:
            send_alert(
                "inject all failed",
                f"attempted={len(attempted)} failed={len(failed)}",
                level="critical",
            )
        elif failed:
            send_alert(
                "inject partial",
                f"ok={len(ok_emails)} failed={len(failed)}",
                level="warning",
            )
    except Exception as e:
        safe_print(f"[WORKFLOW] inject error: {e}")
        send_alert("inject error", str(e), level="critical")
        return 1
    print(f"[WORKFLOW] Finished {now_iso()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
