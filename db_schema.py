#!/usr/bin/env python3
"""Shared SQLite schema for akun.db (accounts + proxy_stats + domain_stats)."""
from __future__ import annotations

import os
import sqlite3
from typing import Optional

from token_util import ensure_probe_columns, ensure_token_columns

DEFAULT_DB = os.path.expanduser("~/grok-farm/akun.db")

# Auto-skip domain after this many consecutive OTP/farm failures (override via env).
DEFAULT_DOMAIN_MAX_CONSECUTIVE_FAILS = 3
# Soft-skip proxy after this many consecutive inject failures (override via env).
DEFAULT_PROXY_MAX_CONSECUTIVE_FAILS = 5

ACCOUNTS_DDL = """
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
CREATE INDEX IF NOT EXISTS idx_status ON accounts(status);
CREATE INDEX IF NOT EXISTS idx_email ON accounts(email);
"""

PROXY_STATS_DDL = """
CREATE TABLE IF NOT EXISTS proxy_stats (
    proxy_key TEXT PRIMARY KEY,
    success_count INTEGER NOT NULL DEFAULT 0,
    fail_count INTEGER NOT NULL DEFAULT 0,
    consecutive_fails INTEGER NOT NULL DEFAULT 0,
    last_success_at TIMESTAMP,
    last_fail_at TIMESTAMP,
    last_email TEXT,
    score REAL NOT NULL DEFAULT 0.5,
    disabled INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_proxy_score ON proxy_stats(score DESC);
"""

DOMAIN_STATS_DDL = """
CREATE TABLE IF NOT EXISTS domain_stats (
    domain TEXT PRIMARY KEY,
    success_count INTEGER NOT NULL DEFAULT 0,
    fail_count INTEGER NOT NULL DEFAULT 0,
    consecutive_fails INTEGER NOT NULL DEFAULT 0,
    last_success_at TIMESTAMP,
    last_fail_at TIMESTAMP,
    last_email TEXT,
    score REAL NOT NULL DEFAULT 0.5,
    disabled INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_domain_score ON domain_stats(score DESC);
"""


def proxy_key(url: str) -> str:
    """Stable key without credentials for scoring."""
    if not url:
        return ""
    # strip user:pass@
    s = str(url).strip().rstrip("/")
    if "://" in s:
        scheme, rest = s.split("://", 1)
        if "@" in rest:
            rest = rest.split("@", 1)[1]
        return f"{scheme}://{rest}"
    return s


def norm_domain(domain: str) -> str:
    d = (domain or "").strip().lower().lstrip("@")
    if "/" in d:
        d = d.split("/")[0]
    if "@" in d:
        d = d.rsplit("@", 1)[-1]
    return d


def domain_max_consecutive_fails() -> int:
    raw = (os.environ.get("GROK_DOMAIN_MAX_CONSECUTIVE_FAILS") or "").strip()
    try:
        n = int(raw) if raw else DEFAULT_DOMAIN_MAX_CONSECUTIVE_FAILS
    except ValueError:
        n = DEFAULT_DOMAIN_MAX_CONSECUTIVE_FAILS
    return max(1, n)


def proxy_max_consecutive_fails() -> int:
    raw = (os.environ.get("GROK_PROXY_MAX_CONSECUTIVE_FAILS") or "").strip()
    try:
        n = int(raw) if raw else DEFAULT_PROXY_MAX_CONSECUTIVE_FAILS
    except ValueError:
        n = DEFAULT_PROXY_MAX_CONSECUTIVE_FAILS
    return max(1, n)


def connect(db_path: Optional[str] = None) -> sqlite3.Connection:
    path = os.path.expanduser(db_path or os.environ.get("GROK_AKUN_DB") or DEFAULT_DB)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_proxy_stats_columns(conn: sqlite3.Connection) -> None:
    """Additive migration: consecutive_fails + disabled on older proxy_stats tables."""
    try:
        cols = {
            str(r[1])
            for r in conn.execute("PRAGMA table_info(proxy_stats)").fetchall()
        }
    except sqlite3.Error:
        return
    if "consecutive_fails" not in cols:
        try:
            conn.execute(
                "ALTER TABLE proxy_stats ADD COLUMN consecutive_fails INTEGER NOT NULL DEFAULT 0"
            )
        except sqlite3.Error:
            pass
    if "disabled" not in cols:
        try:
            conn.execute(
                "ALTER TABLE proxy_stats ADD COLUMN disabled INTEGER NOT NULL DEFAULT 0"
            )
        except sqlite3.Error:
            pass


def migrate(conn: sqlite3.Connection) -> None:
    conn.executescript(ACCOUNTS_DDL)
    conn.executescript(PROXY_STATS_DDL)
    conn.executescript(DOMAIN_STATS_DDL)
    _ensure_proxy_stats_columns(conn)
    ensure_token_columns(conn)
    ensure_probe_columns(conn)
    conn.commit()


def harden_db_file(db_path: Optional[str] = None) -> None:
    path = os.path.expanduser(db_path or os.environ.get("GROK_AKUN_DB") or DEFAULT_DB)
    if os.path.isfile(path):
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass


def _row_counts(row) -> tuple[int, int]:
    """Support sqlite3.Row and plain tuple for success/fail columns."""
    try:
        return int(row["success_count"]), int(row["fail_count"])
    except (TypeError, KeyError, IndexError):
        return int(row[0]), int(row[1])


def record_proxy_result(
    conn: sqlite3.Connection,
    proxy_url: str,
    success: bool,
    email: str = "",
    when_iso: str = "",
) -> None:
    """Track inject outcome per proxy; consecutive_fails + soft disabled (mirror domain_stats)."""
    key = proxy_key(proxy_url)
    if not key:
        return
    row = conn.execute(
        """SELECT success_count, fail_count, consecutive_fails, disabled
           FROM proxy_stats WHERE proxy_key=?""",
        (key,),
    ).fetchone()
    if row is None:
        sc = 1 if success else 0
        fc = 0 if success else 1
        cf = 0 if success else 1
        score = sc / max(sc + fc, 1)
        if success:
            conn.execute(
                """INSERT INTO proxy_stats
                   (proxy_key, success_count, fail_count, consecutive_fails,
                    last_success_at, last_email, score, disabled, updated_at)
                   VALUES (?,?,?,?,?,?,?,0,?)""",
                (key, sc, fc, cf, when_iso, email, score, when_iso),
            )
        else:
            conn.execute(
                """INSERT INTO proxy_stats
                   (proxy_key, success_count, fail_count, consecutive_fails,
                    last_fail_at, last_email, score, disabled, updated_at)
                   VALUES (?,?,?,?,?,?,?,0,?)""",
                (key, sc, fc, cf, when_iso, email, score, when_iso),
            )
        return
    try:
        prev_sc = int(row["success_count"])
        prev_fc = int(row["fail_count"])
        prev_cf = int(row["consecutive_fails"] or 0)
        disabled = int(row["disabled"] or 0)
    except (TypeError, KeyError, IndexError):
        prev_sc, prev_fc = _row_counts(row)
        prev_cf = int(row[2] or 0) if len(row) > 2 else 0
        disabled = int(row[3] or 0) if len(row) > 3 else 0
    sc = prev_sc + (1 if success else 0)
    fc = prev_fc + (0 if success else 1)
    cf = 0 if success else (prev_cf + 1)
    score = sc / max(sc + fc, 1)
    # success re-enables auto-disabled proxies (manual disabled stays until operator clears)
    new_disabled = 0 if success else disabled
    if success:
        conn.execute(
            """UPDATE proxy_stats SET success_count=?, fail_count=?, consecutive_fails=?,
               last_success_at=?, last_email=?, score=?, disabled=?, updated_at=?
               WHERE proxy_key=?""",
            (sc, fc, cf, when_iso, email, score, new_disabled, when_iso, key),
        )
    else:
        conn.execute(
            """UPDATE proxy_stats SET success_count=?, fail_count=?, consecutive_fails=?,
               last_fail_at=?, last_email=?, score=?, disabled=?, updated_at=?
               WHERE proxy_key=?""",
            (sc, fc, cf, when_iso, email, score, new_disabled, when_iso, key),
        )


def proxies_to_skip(
    conn: sqlite3.Connection,
    max_consecutive: Optional[int] = None,
) -> set[str]:
    """Proxy keys that should not be preferred for inject (auto or manual disable).

    Fail-open callers: if skip set would empty the live pool, use full pool.
    Never deletes gateway proxyPools rows.
    """
    thr = max_consecutive if max_consecutive is not None else proxy_max_consecutive_fails()
    out: set[str] = set()
    try:
        rows = conn.execute(
            "SELECT proxy_key, consecutive_fails, disabled FROM proxy_stats"
        ).fetchall()
    except sqlite3.Error:
        return out
    for r in rows:
        try:
            key = str(r["proxy_key"] if not isinstance(r, tuple) else r[0] or "")
            cf = int((r["consecutive_fails"] if not isinstance(r, tuple) else r[1]) or 0)
            disabled = int((r["disabled"] if not isinstance(r, tuple) else r[2]) or 0)
        except (TypeError, KeyError, IndexError):
            continue
        if not key:
            continue
        if disabled or cf >= thr:
            out.add(key)
    return out


def record_domain_result(
    conn: sqlite3.Connection,
    domain: str,
    success: bool,
    email: str = "",
    when_iso: str = "",
) -> None:
    """Track OTP/farm outcome per catch-all domain for auto-skip health."""
    d = norm_domain(domain)
    if not d:
        return
    row = conn.execute(
        """SELECT success_count, fail_count, consecutive_fails, disabled
           FROM domain_stats WHERE domain=?""",
        (d,),
    ).fetchone()
    if row is None:
        sc = 1 if success else 0
        fc = 0 if success else 1
        cf = 0 if success else 1
        score = sc / max(sc + fc, 1)
        if success:
            conn.execute(
                """INSERT INTO domain_stats
                   (domain, success_count, fail_count, consecutive_fails,
                    last_success_at, last_email, score, disabled, updated_at)
                   VALUES (?,?,?,?,?,?,?,0,?)""",
                (d, sc, fc, cf, when_iso, email, score, when_iso),
            )
        else:
            conn.execute(
                """INSERT INTO domain_stats
                   (domain, success_count, fail_count, consecutive_fails,
                    last_fail_at, last_email, score, disabled, updated_at)
                   VALUES (?,?,?,?,?,?,?,0,?)""",
                (d, sc, fc, cf, when_iso, email, score, when_iso),
            )
        return
    try:
        prev_sc = int(row["success_count"])
        prev_fc = int(row["fail_count"])
        prev_cf = int(row["consecutive_fails"] or 0)
        disabled = int(row["disabled"] or 0)
    except (TypeError, KeyError, IndexError):
        prev_sc, prev_fc = int(row[0]), int(row[1])
        prev_cf = int(row[2] or 0) if len(row) > 2 else 0
        disabled = int(row[3] or 0) if len(row) > 3 else 0
    sc = prev_sc + (1 if success else 0)
    fc = prev_fc + (0 if success else 1)
    cf = 0 if success else (prev_cf + 1)
    score = sc / max(sc + fc, 1)
    # success re-enables auto-disabled domains (manual disabled stays until operator clears)
    new_disabled = 0 if success else disabled
    if success:
        conn.execute(
            """UPDATE domain_stats SET success_count=?, fail_count=?, consecutive_fails=?,
               last_success_at=?, last_email=?, score=?, disabled=?, updated_at=?
               WHERE domain=?""",
            (sc, fc, cf, when_iso, email, score, new_disabled, when_iso, d),
        )
    else:
        conn.execute(
            """UPDATE domain_stats SET success_count=?, fail_count=?, consecutive_fails=?,
               last_fail_at=?, last_email=?, score=?, disabled=?, updated_at=?
               WHERE domain=?""",
            (sc, fc, cf, when_iso, email, score, new_disabled, when_iso, d),
        )


def domains_to_skip(
    conn: sqlite3.Connection,
    max_consecutive: Optional[int] = None,
) -> set[str]:
    """Domains that should not be picked for new farms (auto or manual disable)."""
    thr = max_consecutive if max_consecutive is not None else domain_max_consecutive_fails()
    out: set[str] = set()
    try:
        rows = conn.execute(
            "SELECT domain, consecutive_fails, disabled FROM domain_stats"
        ).fetchall()
    except sqlite3.Error:
        return out
    for r in rows:
        try:
            domain = norm_domain(r["domain"] if not isinstance(r, tuple) else r[0])
            cf = int((r["consecutive_fails"] if not isinstance(r, tuple) else r[1]) or 0)
            disabled = int((r["disabled"] if not isinstance(r, tuple) else r[2]) or 0)
        except (TypeError, KeyError, IndexError):
            continue
        if not domain:
            continue
        if disabled or cf >= thr:
            out.add(domain)
    return out


def record_domain_result_standalone(
    domain: str,
    success: bool,
    email: str = "",
    when_iso: str = "",
    db_path: Optional[str] = None,
) -> None:
    """Open DB, migrate, record domain outcome, commit, harden. Safe from farm workers."""
    if not norm_domain(domain):
        return
    try:
        conn = connect(db_path)
        migrate(conn)
        record_domain_result(conn, domain, success, email=email, when_iso=when_iso)
        conn.commit()
        conn.close()
        harden_db_file(db_path)
    except Exception:
        # never break farm path on scoring errors
        pass
