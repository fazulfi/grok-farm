#!/usr/bin/env python3
"""Live JWT probe against xAI API for farm inventory (soft by default).

Usage:
  python3 probe_tokens.py
  python3 probe_tokens.py --dry-run
  python3 probe_tokens.py --json --limit 20
  python3 probe_tokens.py --include-farmed
  python3 probe_tokens.py --no-skip-expired
  python3 probe_tokens.py --mark-error   # hard: status=error notes=needs_relogin

Default: update last_probe_* + needs_relogin meta only; never flip injected→error
unless --mark-error.

Env:
  GROK_AKUN_DB
  GROK_TOKEN_PROBE_URL      (default https://api.x.ai/v1/models)
  GROK_TOKEN_PROBE_TIMEOUT  (seconds, default 10)
  GROK_PROBE_SKIP_EXPIRED   (default true — offline jwt_expired without HTTP)
  GROK_PROBE_DELAY          (seconds between probes, default 0)
  GROK_PROBE_PROGRESS_EVERY (print every N, default 25; 0=off)
  GROK_PROBE_COMMIT_EVERY   (commit every N marks, default 50; 0=end only)
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys

from db_schema import DEFAULT_DB, harden_db_file, migrate
from token_util import (
    DEFAULT_PROBE_TIMEOUT,
    DEFAULT_PROBE_URL,
    probe_accounts,
)


def _cli_probe_url() -> str:
    return (os.environ.get("GROK_TOKEN_PROBE_URL") or DEFAULT_PROBE_URL).strip() or DEFAULT_PROBE_URL


def _cli_probe_timeout() -> float:
    raw = (os.environ.get("GROK_TOKEN_PROBE_TIMEOUT") or "").strip()
    if not raw:
        return DEFAULT_PROBE_TIMEOUT
    try:
        return max(1.0, float(raw))
    except ValueError:
        return DEFAULT_PROBE_TIMEOUT


def _cli_skip_expired() -> bool:
    raw = (os.environ.get("GROK_PROBE_SKIP_EXPIRED") or "").strip().lower()
    if not raw:
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    if raw in ("1", "true", "yes", "on"):
        return True
    return True

DB = os.path.expanduser(os.environ.get("GROK_AKUN_DB") or DEFAULT_DB)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Probe access tokens against xAI API (soft inventory meta by default).",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Probe without writing last_probe_* / needs_relogin",
    )
    p.add_argument("--json", action="store_true", help="JSON stats output")
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Max accounts to probe (default: all matching)",
    )
    p.add_argument(
        "--include-farmed",
        action="store_true",
        help="Also probe status=farmed (default: injected only)",
    )
    p.add_argument(
        "--no-mark",
        action="store_true",
        help="Do not write last_probe_* meta (implies probe-only)",
    )
    p.add_argument(
        "--mark",
        action="store_true",
        default=True,
        help="Write last_probe_* meta (default: on)",
    )
    p.add_argument(
        "--no-skip-expired",
        action="store_true",
        help="Force live HTTP even when offline JWT exp has passed",
    )
    p.add_argument(
        "--mark-error",
        action="store_true",
        help="Hard mark: status=error notes=needs_relogin when probe needs_relogin "
        "(default OFF — soft inventory)",
    )
    p.add_argument(
        "--db",
        default=None,
        help=f"Path to akun.db (default: GROK_AKUN_DB or {DEFAULT_DB})",
    )
    p.add_argument(
        "--timeout",
        type=float,
        default=None,
        help=f"HTTP timeout seconds (default: env or {DEFAULT_PROBE_TIMEOUT})",
    )
    p.add_argument(
        "--url",
        default=None,
        help=f"Probe URL (default: env or {DEFAULT_PROBE_URL})",
    )
    p.add_argument(
        "--delay",
        type=float,
        default=None,
        metavar="SEC",
        help="Sleep between probes (default: env GROK_PROBE_DELAY or 0)",
    )
    p.add_argument(
        "--progress-every",
        type=int,
        default=None,
        metavar="N",
        help="Progress line every N accounts (default: env or 25; 0=off)",
    )
    p.add_argument(
        "--commit-every",
        type=int,
        default=None,
        metavar="N",
        help="Commit every N marked accounts (default: env or 50; 0=end only)",
    )
    p.add_argument(
        "--id-order",
        action="store_true",
        help="Order by id ASC only (default: unprobed first for resume)",
    )
    p.add_argument(
        "--quiet",
        action="store_true",
        help="No progress lines (final summary only)",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    db_path = os.path.expanduser(args.db or DB)

    if not os.path.isfile(db_path):
        print(f"ERROR: akun.db missing at {db_path}", file=sys.stderr)
        return 1

    statuses = ("injected", "farmed") if args.include_farmed else ("injected",)
    # --no-skip-expired forces live HTTP; else env GROK_PROBE_SKIP_EXPIRED (default true)
    skip_expired = False if args.no_skip_expired else _cli_skip_expired()
    mark = not args.no_mark and not args.dry_run

    def _progress(done: int, total: int, st: dict) -> None:
        if args.quiet or args.json:
            return
        by = st.get("by_status") or {}
        print(
            f"  … {done}/{total} "
            f"alive={by.get('alive', 0)} "
            f"needs_relogin={by.get('needs_relogin', 0)} "
            f"jwt_expired={by.get('jwt_expired', 0)} "
            f"net={by.get('network_error', 0)}",
            flush=True,
        )

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    migrate(conn)
    stats = probe_accounts(
        conn,
        statuses=statuses,
        limit=args.limit,
        dry_run=args.dry_run,
        mark=mark,
        skip_expired=skip_expired,
        timeout=args.timeout if args.timeout is not None else _cli_probe_timeout(),
        url=args.url or _cli_probe_url(),
        mark_error=bool(args.mark_error) and not args.dry_run,
        delay=args.delay,
        progress_every=0 if args.quiet else args.progress_every,
        commit_every=args.commit_every,
        unprobed_first=not args.id_order,
        progress_fn=None if (args.quiet or args.json) else _progress,
    )
    conn.close()
    if not args.dry_run:
        harden_db_file(db_path)

    out = {
        "db": db_path,
        "probe_url": args.url or _cli_probe_url(),
        "statuses": list(statuses),
        **stats,
    }

    if args.json:
        print(json.dumps(out, indent=2))
    else:
        mode = "DRY-RUN" if args.dry_run else ("MARK-META" if mark else "PROBE-ONLY")
        hard = " +MARK-ERROR" if args.mark_error else ""
        print(f"[probe_tokens] {mode}{hard} scope={'+'.join(statuses)} db={db_path}")
        print(
            f"  scanned={stats['scanned']} probed={stats['probed']} "
            f"marked={stats['marked']} marked_error={stats['marked_error']} "
            f"delay={stats.get('delay', 0)}"
        )
        by = stats.get("by_status") or {}
        parts = [f"{k}={v}" for k, v in sorted(by.items()) if v]
        if parts:
            print("  by_status:", " ".join(parts))
        if args.dry_run:
            print("  (dry-run: re-run without --dry-run to write last_probe_*)")
        if not args.mark_error and (by.get("needs_relogin") or 0) > 0:
            print(
                f"  note: {by.get('needs_relogin')} need relogin "
                "(meta only; use --mark-error to set status=error)"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
