#!/usr/bin/env python3
"""Reconcile 9router providerConnections vs farm akun.db (dry report by default).

SSH pattern matches workflow.py (GROK_9R_SSH / PORT / KEY).
Remote helper: python3 /root/list_grok_connections.py
  (override path: GROK_9R_LIST_CONNECTIONS)

Buckets:
  in_both_ok            — email in DB (injected/farmed) + active gateway connection
  in_db_not_gateway     — farm has injected (or connection id), gateway missing
  in_gateway_not_db     — orphan gateway email not in akun.db
  jwt_expired_both      — both sides show expired JWT health
  needs_relogin_db      — DB flags needs_relogin (column or notes)
  inactive_gateway      — gateway row isActive=0
  duplicate_emails_gateway — same email appears >1 for a provider

Usage:
  python3 reconcile_9router.py
  python3 reconcile_9router.py --json
  python3 reconcile_9router.py --db ~/grok-farm/akun.db --limit-print 20
  python3 reconcile_9router.py --write-notes
  python3 reconcile_9router.py --write-notes --mark-error   # farmed-only → error

Never deletes gateway connections. Never prints full tokens.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
from collections import defaultdict
from typing import Any, Optional

from db_schema import DEFAULT_DB, harden_db_file, migrate
from log_redact import redact, safe_print

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

REMOTE_LIST = os.environ.get("GROK_9R_LIST_CONNECTIONS") or "/root/list_grok_connections.py"


def fetch_gateway_connections(timeout: int = 60) -> list[dict[str, Any]]:
    """Run remote list_grok_connections.py; parse JSONL (token-free)."""
    r = subprocess.run(
        SSH + ["python3", REMOTE_LIST],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if r.returncode != 0 and not (r.stdout or "").strip():
        err = redact((r.stderr or r.stdout or f"exit {r.returncode}")[:500])
        raise RuntimeError(f"remote list_grok_connections failed: {err}")
    if r.stderr:
        # surface non-fatal stderr redacted
        safe_print(f"[reconcile] remote stderr: {redact(r.stderr[:300])}")

    out: list[dict[str, Any]] = []
    for line in (r.stdout or "").splitlines():
        line = line.strip()
        if not line or line.startswith("ERROR"):
            if line.startswith("ERROR"):
                safe_print(f"[reconcile] remote: {redact(line)}")
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        # defensive: strip any accidental token fields
        for k in list(obj.keys()):
            if k.lower() in (
                "access_token",
                "accesstoken",
                "refresh_token",
                "refreshtoken",
                "apikey",
                "api_key",
                "token",
            ):
                del obj[k]
        out.append(obj)
    return out


def _table_columns(conn: sqlite3.Connection) -> set[str]:
    return {r[1] for r in conn.execute("PRAGMA table_info(accounts)").fetchall()}


def _truthy_needs_relogin(val: Any, notes: str) -> bool:
    if val is not None and val != "":
        if isinstance(val, (int, float)) and int(val) != 0:
            return True
        s = str(val).strip().lower()
        if s in ("1", "true", "yes", "y"):
            return True
    n = (notes or "").lower()
    return "needs_relogin" in n or "relogin" == n.strip()


def load_db_accounts(db_path: str) -> list[dict[str, Any]]:
    if not os.path.isfile(db_path):
        raise FileNotFoundError(f"akun.db missing: {db_path}")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    migrate(conn)
    cols = _table_columns(conn)
    select_cols = [
        "email",
        "status",
        "token_health",
        "notes",
        "grok_cli_connection_id",
        "ninerouter_name",
    ]
    has_needs = "needs_relogin" in cols
    if has_needs:
        select_cols.append("needs_relogin")
    rows = conn.execute(
        f"SELECT {', '.join(select_cols)} FROM accounts ORDER BY email"
    ).fetchall()
    conn.close()

    accounts: list[dict[str, Any]] = []
    for row in rows:
        email = (row["email"] or "").strip().lower()
        if not email:
            continue
        notes = row["notes"] or ""
        needs_raw = row["needs_relogin"] if has_needs else None
        accounts.append(
            {
                "email": email,
                "status": row["status"] or "",
                "token_health": row["token_health"] or "",
                "notes": notes,
                "grok_cli_connection_id": row["grok_cli_connection_id"] or "",
                "ninerouter_name": row["ninerouter_name"] or "",
                "needs_relogin": _truthy_needs_relogin(needs_raw, notes),
            }
        )
    return accounts


def _norm_email(e: str) -> str:
    return (e or "").strip().lower()


def reconcile(
    gateway: list[dict[str, Any]],
    accounts: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build bucket report (emails only — no tokens)."""
    db_by_email = {a["email"]: a for a in accounts}
    db_emails = set(db_by_email.keys())

    # gateway: group by email (prefer grok-cli for health matching)
    by_email: dict[str, list[dict[str, Any]]] = defaultdict(list)
    provider_email_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    inactive: list[dict[str, str]] = []

    for g in gateway:
        email = _norm_email(g.get("email") or "")
        if not email:
            # try name fallback already done remote; skip empty
            continue
        provider = g.get("provider") or ""
        by_email[email].append(g)
        if provider:
            provider_email_counts[provider][email] += 1
        try:
            active = int(g.get("isActive", 1) or 0)
        except (TypeError, ValueError):
            active = 1
        if active == 0:
            inactive.append(
                {
                    "email": email,
                    "provider": provider,
                    "id": str(g.get("id") or ""),
                    "name": str(g.get("name") or ""),
                }
            )

    gateway_emails = set(by_email.keys())

    # Farm "has inject interest": status injected, or connection id set, or farmed
    # (in_db_not_gateway focuses on injected / claimed gateway presence)
    def farm_expects_gateway(a: dict[str, Any]) -> bool:
        if a["status"] == "injected":
            return True
        if a.get("grok_cli_connection_id"):
            return True
        return False

    in_both_ok: list[dict[str, Any]] = []
    in_db_not_gateway: list[dict[str, Any]] = []
    in_gateway_not_db: list[dict[str, Any]] = []
    jwt_expired_both: list[dict[str, Any]] = []
    needs_relogin_db: list[dict[str, Any]] = []

    for email, a in db_by_email.items():
        if a.get("needs_relogin"):
            needs_relogin_db.append(
                {
                    "email": email,
                    "status": a["status"],
                    "token_health": a["token_health"],
                    "notes": a["notes"],
                }
            )

        g_rows = by_email.get(email) or []
        if farm_expects_gateway(a) and not g_rows:
            in_db_not_gateway.append(
                {
                    "email": email,
                    "status": a["status"],
                    "token_health": a["token_health"],
                    "grok_cli_connection_id": a.get("grok_cli_connection_id") or "",
                }
            )
            continue

        if not g_rows:
            continue

        # pick grok-cli row for health if present
        g_cli = next((x for x in g_rows if x.get("provider") == "grok-cli"), g_rows[0])
        g_health = g_cli.get("token_health") or ""
        db_health = a.get("token_health") or ""
        try:
            g_active = int(g_cli.get("isActive", 1) or 0)
        except (TypeError, ValueError):
            g_active = 1

        both_expired = g_health == "expired" and db_health == "expired"
        if both_expired:
            jwt_expired_both.append(
                {
                    "email": email,
                    "status": a["status"],
                    "db_token_health": db_health,
                    "gateway_token_health": g_health,
                    "gateway_id": str(g_cli.get("id") or ""),
                }
            )

        if farm_expects_gateway(a) or a["status"] in ("farmed", "injected"):
            if g_active and g_health not in ("expired", "invalid", "missing"):
                in_both_ok.append(
                    {
                        "email": email,
                        "status": a["status"],
                        "db_token_health": db_health,
                        "gateway_token_health": g_health,
                        "providers": sorted(
                            {str(x.get("provider") or "") for x in g_rows if x.get("provider")}
                        ),
                    }
                )

    for email in sorted(gateway_emails - db_emails):
        g_rows = by_email[email]
        providers = sorted({str(x.get("provider") or "") for x in g_rows if x.get("provider")})
        in_gateway_not_db.append(
            {
                "email": email,
                "providers": providers,
                "ids": [str(x.get("id") or "") for x in g_rows],
                "names": [str(x.get("name") or "") for x in g_rows],
            }
        )

    duplicate_emails_gateway: list[dict[str, Any]] = []
    for provider, counts in sorted(provider_email_counts.items()):
        for email, n in sorted(counts.items()):
            if n > 1:
                ids = [
                    str(x.get("id") or "")
                    for x in by_email[email]
                    if x.get("provider") == provider
                ]
                duplicate_emails_gateway.append(
                    {
                        "email": email,
                        "provider": provider,
                        "count": n,
                        "ids": ids,
                    }
                )

    return {
        "counts": {
            "gateway_connections": len(gateway),
            "gateway_emails": len(gateway_emails),
            "db_accounts": len(accounts),
            "in_both_ok": len(in_both_ok),
            "in_db_not_gateway": len(in_db_not_gateway),
            "in_gateway_not_db": len(in_gateway_not_db),
            "jwt_expired_both": len(jwt_expired_both),
            "needs_relogin_db": len(needs_relogin_db),
            "inactive_gateway": len(inactive),
            "duplicate_emails_gateway": len(duplicate_emails_gateway),
        },
        "in_both_ok": in_both_ok,
        "in_db_not_gateway": in_db_not_gateway,
        "in_gateway_not_db": in_gateway_not_db,
        "jwt_expired_both": jwt_expired_both,
        "needs_relogin_db": needs_relogin_db,
        "inactive_gateway": inactive,
        "duplicate_emails_gateway": duplicate_emails_gateway,
    }


def apply_notes(
    db_path: str,
    report: dict[str, Any],
    *,
    mark_error: bool = False,
) -> dict[str, int]:
    """Soft notes only. --mark-error: farmed rows in in_db_not_gateway → status=error."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    migrate(conn)
    stats = {"notes_gateway_missing": 0, "notes_gateway_orphan": 0, "marked_error": 0}

    for item in report.get("in_db_not_gateway") or []:
        email = item.get("email")
        if not email:
            continue
        row = conn.execute(
            "SELECT status, notes FROM accounts WHERE email=?", (email,)
        ).fetchone()
        if row is None:
            continue
        status = row["status"] or ""
        notes = row["notes"] or ""
        if "gateway_missing" not in notes:
            new_notes = (notes + " gateway_missing").strip() if notes else "gateway_missing"
            if mark_error and status == "farmed":
                conn.execute(
                    "UPDATE accounts SET notes=?, status='error' WHERE email=? AND status='farmed'",
                    (new_notes, email),
                )
                stats["marked_error"] += 1
            else:
                conn.execute(
                    "UPDATE accounts SET notes=? WHERE email=?",
                    (new_notes, email),
                )
            stats["notes_gateway_missing"] += 1
        elif mark_error and status == "farmed":
            conn.execute(
                "UPDATE accounts SET status='error' WHERE email=? AND status='farmed'",
                (email,),
            )
            stats["marked_error"] += 1

    # Orphans exist only on gateway — no DB row. Soft: if email appears later we don't invent rows.
    # If operator wants a trail for emails that exist as error/farmed without inject, skip.
    # Spec: set notes='gateway_orphan' — only when email IS in DB somehow without expectation.
    # For true orphans (gateway only) there is no row. Record count only.
    stats["orphan_gateway_emails"] = len(report.get("in_gateway_not_db") or [])

    # Soft-tag DB rows that match gateway orphans shouldn't happen; instead if status not injected
    # but email is only on gateway... already covered. Optional: tag accounts that appear in
    # gateway with status=error? Leave as report-only for pure orphans.

    conn.commit()
    conn.close()
    harden_db_file(db_path)
    return stats


def _print_bucket(name: str, items: list, limit: int) -> None:
    n = len(items)
    safe_print(f"  {name}: {n}")
    for item in items[:limit]:
        if isinstance(item, dict):
            email = item.get("email", "")
            extra = {k: v for k, v in item.items() if k != "email"}
            safe_print(f"    - {email} {extra if extra else ''}".rstrip())
        else:
            safe_print(f"    - {item}")
    if n > limit:
        safe_print(f"    ... +{n - limit} more")


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Reconcile 9router connections vs akun.db")
    p.add_argument(
        "--db",
        default=os.path.expanduser(os.environ.get("GROK_AKUN_DB") or DEFAULT_DB),
        help="Path to akun.db",
    )
    p.add_argument("--json", action="store_true", help="Machine-readable JSON report")
    p.add_argument(
        "--limit-print",
        type=int,
        default=15,
        metavar="N",
        help="Max samples per bucket in text mode (default 15)",
    )
    p.add_argument(
        "--write-notes",
        action="store_true",
        help="Set notes gateway_missing on in_db_not_gateway (soft)",
    )
    p.add_argument(
        "--mark-error",
        action="store_true",
        help="With --write-notes: also set status=error for farmed in_db_not_gateway only",
    )
    p.add_argument(
        "--timeout",
        type=int,
        default=60,
        help="SSH remote list timeout seconds",
    )
    args = p.parse_args(argv)

    if args.mark_error and not args.write_notes:
        print("ERROR: --mark-error requires --write-notes", file=sys.stderr)
        return 2

    try:
        gateway = fetch_gateway_connections(timeout=args.timeout)
    except Exception as e:
        safe_print(f"[reconcile] FATAL fetch gateway: {e}")
        return 1

    try:
        accounts = load_db_accounts(args.db)
    except Exception as e:
        safe_print(f"[reconcile] FATAL load db: {e}")
        return 1

    report = reconcile(gateway, accounts)
    report["db"] = args.db
    report["remote_list"] = REMOTE_LIST
    report["ssh_target"] = os.environ.get("GROK_9R_SSH") or "root@49.12.82.34"

    write_stats: Optional[dict[str, int]] = None
    if args.write_notes:
        write_stats = apply_notes(args.db, report, mark_error=args.mark_error)
        report["write_notes"] = write_stats

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0

    lim = max(0, args.limit_print)
    safe_print(f"[reconcile] db={args.db}")
    safe_print(f"[reconcile] remote={REMOTE_LIST} target={report['ssh_target']}")
    c = report["counts"]
    safe_print(
        f"[reconcile] gateway_conn={c['gateway_connections']} "
        f"gateway_emails={c['gateway_emails']} db_accounts={c['db_accounts']}"
    )
    for key in (
        "in_both_ok",
        "in_db_not_gateway",
        "in_gateway_not_db",
        "jwt_expired_both",
        "needs_relogin_db",
        "inactive_gateway",
        "duplicate_emails_gateway",
    ):
        _print_bucket(key, report.get(key) or [], lim)

    if write_stats is not None:
        safe_print(f"[reconcile] write-notes: {write_stats}")
    else:
        safe_print("[reconcile] dry report only (pass --write-notes to soft-tag DB)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
