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
    last_fail_reason TEXT,
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
    """Additive migration: consecutive_fails + disabled + last_fail_reason on older tables."""
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
    if "last_fail_reason" not in cols:
        try:
            conn.execute(
                "ALTER TABLE proxy_stats ADD COLUMN last_fail_reason TEXT"
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


# Inject/farm fail taxonomy labels (short, no secrets). Used for proxy_stats.last_fail_reason.
FAIL_CLASSES = frozenset(
    {
        "ok",
        "timeout",
        "proxy_error",
        "auth",
        "region",
        "rate_limit",
        "ssh",
        "gateway",
        "token",
        "unknown",
    }
)


def classify_proxy_fail(text: str = "", *, default: str = "unknown") -> str:
    """Map inject/farm error text to a short fail class (no secrets)."""
    s = (text or "").lower()
    if not s:
        return default if default in FAIL_CLASSES else "unknown"
    if any(x in s for x in ("timeout", "timed out", "deadline", "etimedout")):
        return "timeout"
    if any(
        x in s
        for x in (
            "proxy",
            "tunnel",
            "407",
            "socks",
            "connection refused",
            "econnrefused",
            "network unreachable",
            "proxyerror",
        )
    ):
        return "proxy_error"
    if any(x in s for x in ("401", "403", "unauthorized", "forbidden", "auth", "credential")):
        return "auth"
    if any(x in s for x in ("region", "eu ", "geo", "country", "blocked", "not available in")):
        return "region"
    if any(x in s for x in ("429", "rate limit", "too many", "throttle")):
        return "rate_limit"
    if any(x in s for x in ("ssh", "connection reset", "broken pipe", "host key")):
        return "ssh"
    if any(x in s for x in ("gateway", "9router", "inject", "bulk_inject", "provider")):
        return "gateway"
    if any(x in s for x in ("token", "jwt", "expired", "eyj")):
        return "token"
    return default if default in FAIL_CLASSES else "unknown"


def record_proxy_result(
    conn: sqlite3.Connection,
    proxy_url: str,
    success: bool,
    email: str = "",
    when_iso: str = "",
    fail_reason: str = "",
) -> None:
    """Track inject/farm outcome per proxy; consecutive_fails + soft disabled + fail taxonomy."""
    key = proxy_key(proxy_url)
    if not key:
        return
    reason = ""
    if not success:
        r = (fail_reason or "unknown").strip().lower()[:64]
        reason = r if r in FAIL_CLASSES or r else "unknown"
        if reason not in FAIL_CLASSES:
            reason = classify_proxy_fail(reason)
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
                    last_success_at, last_email, score, disabled, last_fail_reason, updated_at)
                   VALUES (?,?,?,?,?,?,?,0,NULL,?)""",
                (key, sc, fc, cf, when_iso, email, score, when_iso),
            )
        else:
            conn.execute(
                """INSERT INTO proxy_stats
                   (proxy_key, success_count, fail_count, consecutive_fails,
                    last_fail_at, last_email, score, disabled, last_fail_reason, updated_at)
                   VALUES (?,?,?,?,?,?,?,0,?,?)""",
                (key, sc, fc, cf, when_iso, email, score, reason, when_iso),
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
    thr = proxy_max_consecutive_fails()
    # success clears soft disable; fail auto-disables when consecutive_fails hits thr
    if success:
        new_disabled = 0
    else:
        new_disabled = 1 if (disabled or cf >= thr) else 0
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
               last_fail_at=?, last_email=?, score=?, disabled=?, last_fail_reason=?, updated_at=?
               WHERE proxy_key=?""",
            (sc, fc, cf, when_iso, email, score, new_disabled, reason, when_iso, key),
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


def list_soft_skipped_proxies(
    conn: sqlite3.Connection,
    max_consecutive: Optional[int] = None,
) -> list[dict]:
    """Rows that soft-skip would exclude (disabled or consecutive_fails >= thr).

    Returns redacted dicts (proxy_key is host:port only — no credentials).
    Never touches gateway proxyPools.
    """
    thr = max_consecutive if max_consecutive is not None else proxy_max_consecutive_fails()
    out: list[dict] = []
    try:
        rows = conn.execute(
            """SELECT proxy_key, success_count, fail_count, consecutive_fails,
                      score, disabled, last_fail_reason, last_fail_at, updated_at
               FROM proxy_stats
               ORDER BY consecutive_fails DESC, score ASC"""
        ).fetchall()
    except sqlite3.Error:
        return out
    for r in rows:
        try:
            key = str(r["proxy_key"] if not isinstance(r, tuple) else r[0] or "")
            cf = int((r["consecutive_fails"] if not isinstance(r, tuple) else r[3]) or 0)
            disabled = int((r["disabled"] if not isinstance(r, tuple) else r[5]) or 0)
            sc = int((r["success_count"] if not isinstance(r, tuple) else r[1]) or 0)
            fc = int((r["fail_count"] if not isinstance(r, tuple) else r[2]) or 0)
            score = float((r["score"] if not isinstance(r, tuple) else r[4]) or 0)
            reason = str(
                (r["last_fail_reason"] if not isinstance(r, tuple) else r[6]) or ""
            )
        except (TypeError, KeyError, IndexError, ValueError):
            continue
        if not key:
            continue
        if not (disabled or cf >= thr):
            continue
        why = []
        if disabled:
            why.append("disabled")
        if cf >= thr:
            why.append(f"cf>={thr}")
        out.append(
            {
                "proxy_key": key,
                "success_count": sc,
                "fail_count": fc,
                "consecutive_fails": cf,
                "score": round(score, 3),
                "disabled": disabled,
                "last_fail_reason": reason[:64],
                "skip_reason": ",".join(why) or "soft",
            }
        )
    return out


def reenable_proxy(
    conn: sqlite3.Connection,
    key_or_url: str,
    *,
    reset_consecutive: bool = True,
    clear_fail_reason: bool = True,
) -> Optional[str]:
    """Clear soft-skip for one proxy (local akun.db only).

    Matches exact proxy_key or substring of proxy_key (after proxy_key normalize).
    Returns the re-enabled proxy_key, or None if no row matched.
    Never deletes gateway proxyPools rows.
    """
    raw = (key_or_url or "").strip()
    if not raw:
        return None
    want = proxy_key(raw) or raw
    # Prefer exact key match first
    row = conn.execute(
        "SELECT proxy_key FROM proxy_stats WHERE proxy_key=?",
        (want,),
    ).fetchone()
    if row is None:
        # substring match (host:port fragment) — pick unique if possible
        rows = conn.execute(
            "SELECT proxy_key FROM proxy_stats WHERE proxy_key LIKE ?",
            (f"%{want}%",),
        ).fetchall()
        if not rows:
            return None
        if len(rows) > 1:
            # exact-ish: prefer key ending with want or equal host
            keys = [str(r["proxy_key"] if not isinstance(r, tuple) else r[0]) for r in rows]
            exact = [k for k in keys if want in k]
            if len(exact) == 1:
                key = exact[0]
            else:
                # ambiguous — re-enable all matching? caller should use list first
                key = keys[0]
        else:
            key = str(rows[0]["proxy_key"] if not isinstance(rows[0], tuple) else rows[0][0])
    else:
        key = str(row["proxy_key"] if not isinstance(row, tuple) else row[0])
    sets = ["disabled=0"]
    if reset_consecutive:
        sets.append("consecutive_fails=0")
    if clear_fail_reason:
        sets.append("last_fail_reason=NULL")
    sets.append("updated_at=CURRENT_TIMESTAMP")
    conn.execute(
        f"UPDATE proxy_stats SET {', '.join(sets)} WHERE proxy_key=?",
        (key,),
    )
    return key


def reenable_proxies(
    conn: sqlite3.Connection,
    *,
    match: str = "",
    all_skipped: bool = False,
    reset_consecutive: bool = True,
    max_consecutive: Optional[int] = None,
) -> list[str]:
    """Re-enable soft-skipped proxies. Local DB only; never gateway DELETE.

    - all_skipped=True: every currently soft-skipped row
    - match: substring of proxy_key (required if not all_skipped)
    Returns list of re-enabled proxy_keys.
    """
    thr = max_consecutive if max_consecutive is not None else proxy_max_consecutive_fails()
    reenabled: list[str] = []
    if all_skipped:
        skipped = list_soft_skipped_proxies(conn, max_consecutive=thr)
        for item in skipped:
            k = reenable_proxy(
                conn,
                item["proxy_key"],
                reset_consecutive=reset_consecutive,
            )
            if k:
                reenabled.append(k)
        return reenabled
    m = (match or "").strip()
    if not m:
        return reenabled
    # re-enable all rows matching substring that are currently skipped OR any match
    want = proxy_key(m) or m
    rows = conn.execute(
        "SELECT proxy_key, consecutive_fails, disabled FROM proxy_stats WHERE proxy_key LIKE ?",
        (f"%{want}%",),
    ).fetchall()
    for r in rows:
        key = str(r["proxy_key"] if not isinstance(r, tuple) else r[0] or "")
        if not key:
            continue
        k = reenable_proxy(conn, key, reset_consecutive=reset_consecutive)
        if k:
            reenabled.append(k)
    return reenabled


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


def record_proxy_result_standalone(
    proxy_url: str,
    success: bool,
    email: str = "",
    when_iso: str = "",
    fail_reason: str = "",
    db_path: Optional[str] = None,
) -> None:
    """Open DB, migrate, record proxy outcome, commit, harden. Safe from farm workers."""
    if not proxy_key(proxy_url):
        return
    try:
        conn = connect(db_path)
        migrate(conn)
        record_proxy_result(
            conn,
            proxy_url,
            success,
            email=email,
            when_iso=when_iso,
            fail_reason=fail_reason,
        )
        conn.commit()
        conn.close()
        harden_db_file(db_path)
    except Exception:
        # never break farm/inject path on scoring errors
        pass
