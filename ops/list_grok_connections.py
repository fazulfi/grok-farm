#!/usr/bin/env python3
"""List grok-cli / xai providerConnections from 9router SQLite (gateway host).

Prints JSONL — never full tokens; only health / exp / has_token.

Usage (on 9router host):
  python3 /root/list_grok_connections.py
  python3 /root/list_grok_connections.py --provider grok-cli
  python3 /root/list_grok_connections.py --provider xai

Env:
  GROK_9R_DB  override DB path (default /var/lib/9router/db/data.sqlite)
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sqlite3
import sys
import time
from typing import Any, Optional

DEFAULT_DB = "/var/lib/9router/db/data.sqlite"
PROVIDERS = ("grok-cli", "xai")
WARN_SECONDS = 3600


def _b64url_decode(segment: str) -> bytes:
    pad = "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(segment + pad)


def decode_jwt_payload(token: str) -> Optional[dict[str, Any]]:
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
    payload = decode_jwt_payload(token)
    if not payload:
        return None
    exp = payload.get("exp")
    try:
        return int(exp) if exp is not None else None
    except (TypeError, ValueError):
        return None


def token_health(token: str, now: Optional[float] = None) -> str:
    """ok | expiring_soon | expired | invalid | missing — no full token."""
    if not token:
        return "missing"
    exp = jwt_exp_unix(token)
    if exp is None:
        if str(token).startswith("eyJ"):
            return "ok"
        # non-JWT secret (e.g. paid xAI api key) — has material but not JWT-exp
        if len(str(token).strip()) > 8:
            return "ok"
        return "invalid"
    ts = now if now is not None else time.time()
    if exp <= ts:
        return "expired"
    if exp - ts <= WARN_SECONDS:
        return "expiring_soon"
    return "ok"


def extract_token(provider: str, data_raw: Any) -> str:
    """Pull accessToken (grok-cli) or apiKey (xai) without logging it."""
    if not data_raw:
        return ""
    try:
        data = json.loads(data_raw) if isinstance(data_raw, str) else data_raw
    except Exception:
        return ""
    if not isinstance(data, dict):
        return ""
    if provider == "grok-cli":
        return (data.get("accessToken") or data.get("access_token") or "") or ""
    if provider == "xai":
        return (data.get("apiKey") or data.get("api_key") or "") or ""
    return ""


def resolve_email(provider: str, email_col: Optional[str], name: Optional[str]) -> str:
    email = (email_col or "").strip()
    if email:
        return email
    name = (name or "").strip()
    if not name:
        return ""
    if provider == "xai" and name.lower().startswith("grok "):
        return name[5:].strip()
    return name


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description="List grok-cli/xai connections (no tokens)")
    p.add_argument(
        "--provider",
        choices=list(PROVIDERS) + ["both"],
        default="both",
        help="Filter provider (default: both)",
    )
    p.add_argument(
        "--db",
        default=os.environ.get("GROK_9R_DB") or DEFAULT_DB,
        help="9router SQLite path",
    )
    args = p.parse_args(argv)

    providers = list(PROVIDERS) if args.provider == "both" else [args.provider]
    db_path = args.db
    if not os.path.isfile(db_path):
        print(f"ERROR: db missing: {db_path}", file=sys.stderr)
        return 1

    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
    except Exception as e:
        print(f"ERROR: open db: {e}", file=sys.stderr)
        return 1

    placeholders = ",".join("?" for _ in providers)
    try:
        rows = conn.execute(
            f"""SELECT id, provider, name, email, isActive, data
                FROM providerConnections
                WHERE provider IN ({placeholders})
                ORDER BY provider, name""",
            providers,
        ).fetchall()
    except Exception as e:
        print(f"ERROR: query: {e}", file=sys.stderr)
        conn.close()
        return 1
    conn.close()

    now = time.time()
    for row in rows:
        provider = row["provider"] or ""
        name = row["name"] or ""
        email = resolve_email(provider, row["email"], name)
        token = extract_token(provider, row["data"])
        exp = jwt_exp_unix(token) if token and str(token).startswith("eyJ") else None
        health = token_health(token, now=now)
        has_token = bool(token and str(token).strip())
        is_active = row["isActive"]
        try:
            is_active_out = int(is_active) if is_active is not None else 0
        except (TypeError, ValueError):
            is_active_out = 1 if is_active else 0

        out = {
            "email": email,
            "provider": provider,
            "id": row["id"] or "",
            "isActive": is_active_out,
            "name": name,
            "token_health": health,
            "token_exp": exp,
            "has_token": has_token,
        }
        # Never include token material
        print(json.dumps(out, separators=(",", ":"), ensure_ascii=False))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
