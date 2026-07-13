#!/usr/bin/env python3
"""JWT helpers for farm OAuth tokens (no signature verify — exp/iat only)."""
from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Optional, Sequence

# Live probe against xAI API (stdlib only — no new deps).
DEFAULT_PROBE_URL = "https://api.x.ai/v1/models"
DEFAULT_PROBE_TIMEOUT = 10.0

# Probe status enum (soft inventory; does not flip account status unless CLI asks).
PROBE_STATUSES = (
    "alive",
    "needs_relogin",
    "rate_limited",
    "spend_limited",
    "network_error",
    "invalid",
    "missing",
    "jwt_expired",
)

_SPEND_HINTS = (
    "spend",
    "quota",
    "credit",
    "billing",
    "usage limit",
    "usage_limit",
    "exceeded",
    "insufficient",
    "payment",
)


def _b64url_decode(segment: str) -> bytes:
    pad = "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(segment + pad)


def decode_jwt_payload(token: str) -> Optional[dict[str, Any]]:
    """Return JWT payload dict or None if not a parseable JWT."""
    if not token or not isinstance(token, str):
        return None
    parts = token.strip().split(".")
    if len(parts) < 2 or not parts[0].startswith("eyJ"):
        return None
    try:
        raw = _b64url_decode(parts[1])
        data = json.loads(raw.decode("utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def jwt_exp_unix(token: str) -> Optional[int]:
    """Return exp claim as unix seconds, or None."""
    payload = decode_jwt_payload(token)
    if not payload:
        return None
    exp = payload.get("exp")
    try:
        return int(exp) if exp is not None else None
    except (TypeError, ValueError):
        return None


def token_health(token: str, now: Optional[float] = None, warn_seconds: int = 3600) -> str:
    """
    Classify access token health:
      ok | expiring_soon | expired | invalid | missing
    """
    if not token:
        return "missing"
    exp = jwt_exp_unix(token)
    if exp is None:
        # Still looks like JWT but no exp — treat as ok-unknown
        if str(token).startswith("eyJ"):
            return "ok"
        return "invalid"
    ts = now if now is not None else time.time()
    if exp <= ts:
        return "expired"
    if exp - ts <= warn_seconds:
        return "expiring_soon"
    return "ok"


def ensure_token_columns(conn) -> None:
    """Add token_exp / token_health columns if missing (idempotent)."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(accounts)").fetchall()}
    if "token_exp" not in cols:
        conn.execute("ALTER TABLE accounts ADD COLUMN token_exp INTEGER")
    if "token_health" not in cols:
        conn.execute("ALTER TABLE accounts ADD COLUMN token_health TEXT")


def ensure_probe_columns(conn) -> None:
    """Add live-probe meta columns if missing (idempotent, additive)."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(accounts)").fetchall()}
    if "last_probe_at" not in cols:
        conn.execute("ALTER TABLE accounts ADD COLUMN last_probe_at TEXT")
    if "last_probe_status" not in cols:
        conn.execute("ALTER TABLE accounts ADD COLUMN last_probe_status TEXT")
    if "last_probe_http" not in cols:
        conn.execute("ALTER TABLE accounts ADD COLUMN last_probe_http INTEGER")
    if "needs_relogin" not in cols:
        conn.execute(
            "ALTER TABLE accounts ADD COLUMN needs_relogin INTEGER DEFAULT 0"
        )


def update_account_token_meta(conn, email: str, access_token: str, warn_seconds: int = 3600) -> str:
    """Write token_exp + token_health for one account; return health label."""
    ensure_token_columns(conn)
    exp = jwt_exp_unix(access_token)
    health = token_health(access_token, warn_seconds=warn_seconds)
    conn.execute(
        "UPDATE accounts SET token_exp=?, token_health=? WHERE email=?",
        (exp, health, email),
    )
    return health


def _probe_url() -> str:
    return (os.environ.get("GROK_TOKEN_PROBE_URL") or DEFAULT_PROBE_URL).strip() or DEFAULT_PROBE_URL


def _probe_timeout(default: float = DEFAULT_PROBE_TIMEOUT) -> float:
    raw = (os.environ.get("GROK_TOKEN_PROBE_TIMEOUT") or "").strip()
    if not raw:
        return default
    try:
        return max(1.0, float(raw))
    except ValueError:
        return default


def _env_bool(name: str, default: bool = True) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    if raw in ("0", "false", "no", "off"):
        return False
    if raw in ("1", "true", "yes", "on"):
        return True
    return default


def _body_spend_limited(body: str) -> bool:
    low = (body or "").lower()
    return any(h in low for h in _SPEND_HINTS)


def _probe_result(
    status: str,
    *,
    http_code: Optional[int] = None,
    error: Optional[str] = None,
    offline_health: Optional[str] = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "http_code": http_code,
        "error": error,
        "offline_health": offline_health,
    }


def probe_access_token(
    access_token: str,
    *,
    timeout: float = DEFAULT_PROBE_TIMEOUT,
    url: Optional[str] = None,
    skip_expired: Optional[bool] = None,
) -> dict[str, Any]:
    """
    Live HTTP probe of an access token against xAI models endpoint.

    Returns dict keys: status, http_code, error, offline_health.
    Status: alive|needs_relogin|rate_limited|spend_limited|network_error|
            invalid|missing|jwt_expired

    Never logs the full JWT.
    """
    health = token_health(access_token)
    if skip_expired is None:
        skip_expired = _env_bool("GROK_PROBE_SKIP_EXPIRED", True)

    if not access_token:
        return _probe_result("missing", offline_health=health, error="empty token")
    if health == "missing":
        return _probe_result("missing", offline_health=health, error="empty token")
    if health == "invalid":
        return _probe_result("invalid", offline_health=health, error="not a JWT")
    if health == "expired" and skip_expired:
        return _probe_result(
            "jwt_expired",
            offline_health=health,
            error="offline exp passed; skipped HTTP",
        )

    probe_url = (url or _probe_url()).strip() or DEFAULT_PROBE_URL
    to = float(timeout) if timeout is not None else _probe_timeout()
    req = urllib.request.Request(
        probe_url,
        method="GET",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            "User-Agent": "grok-farm-probe/2.2",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=to) as resp:
            code = int(getattr(resp, "status", None) or resp.getcode() or 0)
            try:
                body = resp.read(4096).decode("utf-8", errors="replace")
            except Exception:
                body = ""
            if 200 <= code <= 299:
                if _body_spend_limited(body):
                    return _probe_result(
                        "spend_limited",
                        http_code=code,
                        offline_health=health,
                        error="spend/quota hint in 2xx body",
                    )
                return _probe_result("alive", http_code=code, offline_health=health)
            # Unexpected non-error path
            return _probe_result(
                "network_error",
                http_code=code,
                offline_health=health,
                error=f"unexpected status {code}",
            )
    except urllib.error.HTTPError as e:
        code = int(e.code or 0)
        try:
            body = e.read(4096).decode("utf-8", errors="replace")
        except Exception:
            body = ""
        if code in (401, 403):
            return _probe_result(
                "needs_relogin",
                http_code=code,
                offline_health=health,
                error=f"HTTP {code}",
            )
        if code == 429:
            return _probe_result(
                "rate_limited",
                http_code=code,
                offline_health=health,
                error="HTTP 429",
            )
        if code == 402 or _body_spend_limited(body):
            return _probe_result(
                "spend_limited",
                http_code=code,
                offline_health=health,
                error=f"HTTP {code} spend/quota",
            )
        if 500 <= code <= 599:
            return _probe_result(
                "network_error",
                http_code=code,
                offline_health=health,
                error=f"server HTTP {code}",
            )
        return _probe_result(
            "network_error",
            http_code=code,
            offline_health=health,
            error=f"HTTP {code}",
        )
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", e)
        return _probe_result(
            "network_error",
            offline_health=health,
            error=f"urlerror: {reason}",
        )
    except TimeoutError:
        return _probe_result(
            "network_error",
            offline_health=health,
            error="timeout",
        )
    except Exception as e:
        return _probe_result(
            "network_error",
            offline_health=health,
            error=f"{type(e).__name__}: {e}",
        )


def update_account_probe(
    conn,
    email: str,
    result: dict[str, Any],
    *,
    mark: bool = True,
) -> None:
    """
    Persist last_probe_* meta for one account.

    needs_relogin:
      - set 1 if status == needs_relogin
      - set 0 if status == alive
      - leave unchanged on network_error / rate_limited / spend_limited / offline skips
    """
    ensure_probe_columns(conn)
    if not mark:
        return
    status = str(result.get("status") or "")
    http_code = result.get("http_code")
    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        http_int = int(http_code) if http_code is not None else None
    except (TypeError, ValueError):
        http_int = None

    if status == "needs_relogin":
        conn.execute(
            """UPDATE accounts SET last_probe_at=?, last_probe_status=?,
               last_probe_http=?, needs_relogin=1 WHERE email=?""",
            (now_iso, status, http_int, email),
        )
    elif status == "alive":
        conn.execute(
            """UPDATE accounts SET last_probe_at=?, last_probe_status=?,
               last_probe_http=?, needs_relogin=0 WHERE email=?""",
            (now_iso, status, http_int, email),
        )
    else:
        # Do not clear needs_relogin on transient / offline classifications
        conn.execute(
            """UPDATE accounts SET last_probe_at=?, last_probe_status=?,
               last_probe_http=? WHERE email=?""",
            (now_iso, status, http_int, email),
        )


def _probe_delay_seconds() -> float:
    """Inter-request delay for large batches (env GROK_PROBE_DELAY, default 0)."""
    raw = (os.environ.get("GROK_PROBE_DELAY") or "").strip()
    if not raw:
        return 0.0
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 0.0


def _probe_progress_every() -> int:
    """Print progress every N accounts (env GROK_PROBE_PROGRESS_EVERY, default 25; 0=off)."""
    raw = (os.environ.get("GROK_PROBE_PROGRESS_EVERY") or "").strip()
    if not raw:
        return 25
    try:
        return max(0, int(raw))
    except ValueError:
        return 25


def _probe_commit_every() -> int:
    """Commit every N marked accounts (env GROK_PROBE_COMMIT_EVERY, default 50; 0=end only)."""
    raw = (os.environ.get("GROK_PROBE_COMMIT_EVERY") or "").strip()
    if not raw:
        return 50
    try:
        return max(0, int(raw))
    except ValueError:
        return 50


def probe_accounts(
    conn,
    *,
    statuses: Sequence[str] = ("injected",),
    limit: Optional[int] = None,
    dry_run: bool = False,
    mark: bool = True,
    skip_expired: bool = True,
    timeout: Optional[float] = None,
    url: Optional[str] = None,
    mark_error: bool = False,
    delay: Optional[float] = None,
    progress_every: Optional[int] = None,
    commit_every: Optional[int] = None,
    unprobed_first: bool = True,
    progress_fn: Optional[Any] = None,
) -> dict[str, Any]:
    """
    Probe accounts with given statuses (sequential). Soft by default:
    updates last_probe_* meta only; never flips injected→error unless mark_error.

    Large-batch options:
      - delay: sleep between HTTP probes (skip offline short-circuit still counts)
      - progress_every: call progress_fn every N accounts
      - commit_every: periodic commit so long runs are resume-safe
      - unprobed_first: prefer NULL last_probe_at then older id (stable resume)

    Returns stats dict with counts per status + scanned/probed/marked/errors.
    """
    ensure_token_columns(conn)
    ensure_probe_columns(conn)
    status_list = tuple(s for s in statuses if s)
    if not status_list:
        status_list = ("injected",)
    placeholders = ",".join("?" for _ in status_list)
    # Resume-friendly order: never-probed first, then by id (stable for --limit slices)
    if unprobed_first:
        order = (
            "ORDER BY CASE WHEN last_probe_at IS NULL OR last_probe_at='' "
            "THEN 0 ELSE 1 END, id ASC"
        )
    else:
        order = "ORDER BY id ASC"
    sql = f"""SELECT email, access_token, status FROM accounts
              WHERE status IN ({placeholders})
              {order}"""
    rows = conn.execute(sql, status_list).fetchall()
    if limit is not None and limit > 0:
        rows = rows[: int(limit)]

    to = timeout if timeout is not None else _probe_timeout()
    dly = float(delay) if delay is not None else _probe_delay_seconds()
    prog_n = (
        int(progress_every)
        if progress_every is not None
        else _probe_progress_every()
    )
    commit_n = (
        int(commit_every)
        if commit_every is not None
        else _probe_commit_every()
    )
    stats: dict[str, Any] = {
        "scanned": 0,
        "probed": 0,
        "marked": 0,
        "marked_error": 0,
        "dry_run": 1 if dry_run else 0,
        "skip_expired": 1 if skip_expired else 0,
        "delay": dly,
        "commit_every": commit_n,
        "unprobed_first": 1 if unprobed_first else 0,
        "by_status": {s: 0 for s in PROBE_STATUSES},
    }
    total = len(rows)
    since_commit = 0

    for row in rows:
        if hasattr(row, "keys"):
            email = row["email"]
            at = row["access_token"] or ""
            acct_status = row["status"]
        else:
            email, at, acct_status = row[0], row[1] or "", row[2]
        stats["scanned"] += 1
        result = probe_access_token(
            at,
            timeout=to,
            url=url,
            skip_expired=skip_expired,
        )
        st = str(result.get("status") or "invalid")
        stats["probed"] += 1
        stats["by_status"][st] = stats["by_status"].get(st, 0) + 1

        if not dry_run:
            if mark:
                update_account_probe(conn, email, result, mark=True)
                stats["marked"] += 1
                since_commit += 1

            # Optional hard mark: only when operator passes mark_error
            if mark_error and st == "needs_relogin" and acct_status in (
                "injected",
                "farmed",
            ):
                conn.execute(
                    """UPDATE accounts SET status='error', notes=?
                       WHERE email=? AND status=?""",
                    ("needs_relogin", email, acct_status),
                )
                stats["marked_error"] += 1

            if commit_n > 0 and since_commit >= commit_n:
                conn.commit()
                since_commit = 0

        if prog_n > 0 and progress_fn and (stats["scanned"] % prog_n == 0):
            try:
                progress_fn(stats["scanned"], total, stats)
            except Exception:
                pass

        # Rate-limit live HTTP path slightly; offline jwt_expired still delayed
        # so large batches do not hammer api.x.ai when skip_expired is false.
        if dly > 0 and stats["scanned"] < total:
            time.sleep(dly)

    if not dry_run:
        conn.commit()
    return stats


def mark_expired_accounts(
    conn,
    *,
    include_injected: bool = False,
    dry_run: bool = False,
    warn_seconds: int = 3600,
) -> dict[str, int]:
    """
    Refresh JWT meta and mark dead tokens as status=error.

    Policy (enterprise):
      - Always: farmed + expired/invalid/missing → status=error, notes=token_expired|bad_token
      - Optional include_injected: injected + expired → status=error (inventory cleanup;
        does not revoke 9router connections — operator re-farm / re-auth separately)

    Returns counts: scanned, updated_meta, marked_error, farmed_expired, injected_expired, dry_run.
    """
    ensure_token_columns(conn)
    rows = conn.execute(
        """SELECT email, access_token, status FROM accounts
           WHERE status IN ('farmed', 'injected')"""
    ).fetchall()
    stats = {
        "scanned": 0,
        "updated_meta": 0,
        "marked_error": 0,
        "farmed_expired": 0,
        "injected_expired": 0,
        "dry_run": 1 if dry_run else 0,
    }
    for row in rows:
        if hasattr(row, "keys"):
            email, at, status = row["email"], row["access_token"] or "", row["status"]
        else:
            email, at, status = row[0], row[1] or "", row[2]
        stats["scanned"] += 1
        health = token_health(at, warn_seconds=warn_seconds)
        exp = jwt_exp_unix(at)
        if not dry_run:
            conn.execute(
                "UPDATE accounts SET token_exp=?, token_health=? WHERE email=?",
                (exp, health, email),
            )
            stats["updated_meta"] += 1

        note = None
        if not at or not str(at).startswith("eyJ") or health == "invalid":
            note = "bad_token"
        elif health == "expired":
            note = "token_expired"
        else:
            continue

        if status == "farmed":
            stats["farmed_expired"] += 1
            if not dry_run:
                conn.execute(
                    "UPDATE accounts SET status='error', notes=? WHERE email=? AND status='farmed'",
                    (note, email),
                )
                stats["marked_error"] += 1
        elif status == "injected" and include_injected and note == "token_expired":
            stats["injected_expired"] += 1
            if not dry_run:
                conn.execute(
                    "UPDATE accounts SET status='error', notes=? WHERE email=? AND status='injected'",
                    (note, email),
                )
                stats["marked_error"] += 1
        elif status == "injected" and note == "token_expired":
            stats["injected_expired"] += 1
            # meta only unless include_injected

    if not dry_run:
        conn.commit()
    return stats
