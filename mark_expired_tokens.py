#!/usr/bin/env python3
"""Mark expired/invalid farmed (and optionally injected) JWTs as status=error.

Usage:
  python3 mark_expired_tokens.py              # farmed only (default)
  python3 mark_expired_tokens.py --dry-run
  python3 mark_expired_tokens.py --include-injected
  python3 mark_expired_tokens.py --json
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys

from db_schema import DEFAULT_DB, harden_db_file, migrate
from token_util import mark_expired_accounts

DB = os.path.expanduser(os.environ.get("GROK_AKUN_DB") or DEFAULT_DB)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    dry = "--dry-run" in argv
    include_injected = "--include-injected" in argv
    want_json = "--json" in argv

    if not os.path.isfile(DB):
        print(f"ERROR: akun.db missing at {DB}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    migrate(conn)
    stats = mark_expired_accounts(
        conn, include_injected=include_injected, dry_run=dry
    )
    conn.close()
    if not dry:
        harden_db_file(DB)

    if want_json:
        print(json.dumps({"db": DB, **stats}, indent=2))
    else:
        mode = "DRY-RUN" if dry else "APPLY"
        scope = "farmed+injected" if include_injected else "farmed"
        print(f"[mark_expired] {mode} scope={scope} db={DB}")
        print(
            f"  scanned={stats['scanned']} meta={stats['updated_meta']} "
            f"marked_error={stats['marked_error']} "
            f"farmed_dead={stats['farmed_expired']} "
            f"injected_expired={stats['injected_expired']}"
        )
        if dry and stats["farmed_expired"]:
            print("  (dry-run: re-run without --dry-run to mark farmed dead tokens)")
        if not include_injected and stats["injected_expired"]:
            print(
                f"  note: {stats['injected_expired']} injected JWTs are expired "
                "(meta refreshed; use --include-injected to set status=error)"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
