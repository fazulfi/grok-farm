#!/usr/bin/env python3
"""JWT helpers for farm OAuth tokens (no signature verify — exp/iat only)."""
from __future__ import annotations

import base64
import json
import time
from typing import Any, Optional


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
