#!/usr/bin/env python3
"""Operator CLI: list / re-enable soft-skipped proxies in local akun.db.

Soft-skip is farm-side only (proxy_stats.disabled / consecutive_fails).
NEVER deletes 9router proxyPools rows. NEVER gateway write-back.

Usage:
  python reenable_proxy.py --list
  python reenable_proxy.py --match host:port
  python reenable_proxy.py --all
  python reenable_proxy.py --all --dry-run
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

# Load .env when present (same pattern as alerts/check_status)
try:
    from dotenv import load_dotenv

    load_dotenv(os.path.expanduser("~/grok-farm/.env"), override=False)
    load_dotenv(".env", override=False)
except ImportError:
    pass

from db_schema import (
    connect,
    harden_db_file,
    list_soft_skipped_proxies,
    migrate,
    proxy_max_consecutive_fails,
    reenable_proxies,
)


def _db_path(arg: str | None) -> str:
    return os.path.expanduser(
        arg or os.environ.get("GROK_AKUN_DB") or "~/grok-farm/akun.db"
    )


def main() -> int:
    p = argparse.ArgumentParser(
        description="List or re-enable soft-skipped proxies (local akun.db only)"
    )
    p.add_argument(
        "--list",
        action="store_true",
        help="List soft-skipped / disabled proxies (default if no re-enable flag)",
    )
    p.add_argument(
        "--match",
        default="",
        help="Substring of proxy_key (host:port) to re-enable",
    )
    p.add_argument(
        "--all",
        action="store_true",
        dest="all_skipped",
        help="Re-enable all currently soft-skipped proxies",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be re-enabled; do not write",
    )
    p.add_argument(
        "--keep-consecutive",
        action="store_true",
        help="Do not reset consecutive_fails (only clear disabled)",
    )
    p.add_argument("--db", default="", help="Path to akun.db")
    p.add_argument("--json", action="store_true", help="Machine-readable output")
    args = p.parse_args()

    db = _db_path(args.db or None)
    thr = proxy_max_consecutive_fails()
    conn = connect(db)
    migrate(conn)

    skipped = list_soft_skipped_proxies(conn, max_consecutive=thr)
    do_reenable = bool(args.match.strip()) or args.all_skipped
    if not do_reenable:
        args.list = True

    report: dict[str, Any] = {
        "db": db,
        "threshold": thr,
        "soft_skipped_count": len(skipped),
        "soft_skipped": skipped,
        "dry_run": args.dry_run,
        "reenabled": [],
    }

    if args.list and not do_reenable:
        if args.json:
            print(json.dumps(report, indent=2))
        else:
            print(f"db={db} thr={thr} soft_skipped={len(skipped)}")
            if not skipped:
                print("(none)")
            for item in skipped:
                print(
                    f"  {item['proxy_key']}  "
                    f"score={item['score']} cf={item['consecutive_fails']} "
                    f"disabled={item['disabled']} "
                    f"reason={item.get('last_fail_reason') or '-'} "
                    f"[{item['skip_reason']}]"
                )
        conn.close()
        return 0

    if args.dry_run:
        if args.all_skipped:
            keys = [s["proxy_key"] for s in skipped]
        else:
            m = args.match.strip()
            keys = [s["proxy_key"] for s in skipped if m in s["proxy_key"]]
            if not keys:
                # would still match any row via reenable_proxies
                keys = [f"(match {m!r} — may hit non-skipped rows)"]
        report["would_reenable"] = keys
        if args.json:
            print(json.dumps(report, indent=2))
        else:
            print(f"DRY_RUN would re-enable {len(keys)}:")
            for k in keys:
                print(f"  {k}")
        conn.close()
        return 0

    keys = reenable_proxies(
        conn,
        match=args.match,
        all_skipped=args.all_skipped,
        reset_consecutive=not args.keep_consecutive,
        max_consecutive=thr,
    )
    conn.commit()
    report["reenabled"] = keys
    report["soft_skipped_after"] = len(
        list_soft_skipped_proxies(conn, max_consecutive=thr)
    )
    conn.close()
    harden_db_file(db)

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"reenabled={len(keys)} thr={thr} db={db}")
        for k in keys:
            print(f"  OK {k}")
        if not keys:
            print("(no matching proxy_stats rows)")
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
