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
