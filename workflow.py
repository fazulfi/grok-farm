#!/usr/bin/env python3
"""Unified workflow: import DB + inject farmed accounts using proxies from 9router proxyPools only."""
from __future__ import annotations

import json
import os
import random
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# Ensure Telegram / webhook env available even if parent shell did not export .env
try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent / ".env", override=False)
except ImportError:
    pass

from alerts import send_alert
from db_schema import (
    DEFAULT_DB,
    harden_db_file,
    migrate,
    proxy_key,
    proxies_to_skip,
    record_proxy_result,
)
from log_redact import redact, redact_proxy_url, safe_print


def _notify_inject_event(
    title: str,
    body: str,
    *,
    level: str = "info",
    extra: Optional[dict] = None,
    ok_emails: Optional[list] = None,
    fail_emails: Optional[list] = None,
    fail_class: str = "",
) -> None:
    """Refresh sticky ops dashboard (default) or send new alert if batch alerts on.

    Never includes JWT/password (S7). HUD remains primary local UI.
    """
    extra = dict(extra or {})
    ok_n = int(extra.get("ok") or (len(ok_emails) if ok_emails else 0) or 0)
    fail_n = int(
        extra.get("fail")
        or extra.get("failed")
        or (len(fail_emails) if fail_emails else 0)
        or 0
    )
    try:
        from daily_digest import batch_alerts_enabled, ops_dashboard_enabled, publish_ops_event

        if ops_dashboard_enabled() and not batch_alerts_enabled():
            publish_ops_event(
                {
                    "kind": "inject",
                    "ok": ok_n,
                    "fail": fail_n,
                    "level": level,
                    "title": title,
                    "fail_class": fail_class or extra.get("fail_class") or "",
                    "ok_emails": list(ok_emails or []),
                    "fail_emails": list(fail_emails or []),
                    "note": (body or "")[:200],
                }
            )
            return
    except Exception as e:
        safe_print(f"[WORKFLOW] ops dashboard inject refresh failed: {e}")

    send_alert(title, body, level=level, extra=extra, skip_debounce=True)
from token_util import token_health, update_account_token_meta

CSA_DB = os.path.expanduser(os.environ.get("GROK_AKUN_DB") or DEFAULT_DB)
RESULTS = os.path.expanduser(os.environ.get("GROK_RESULTS_DIR") or "~/grok-farm/results")
# Accept both GROK_9R_KEY / GROK_9R_PORT (live .env) and GROK_9R_SSH_KEY / GROK_9R_SSH_PORT
_SSH_KEY = os.environ.get("GROK_9R_SSH_KEY") or os.environ.get("GROK_9R_KEY") or "~/.ssh/id_ed25519"
_SSH_PORT = os.environ.get("GROK_9R_SSH_PORT") or os.environ.get("GROK_9R_PORT") or "39999"
SSH = [
    "ssh",
    "-i",
    os.path.expanduser(_SSH_KEY),
    "-o",
    "StrictHostKeyChecking=no",
    "-o",
    "ConnectTimeout=15",
    os.environ.get("GROK_9R_SSH") or "root@49.12.82.34",
    "-p",
    _SSH_PORT,
]


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


def import_batches() -> int:
    import glob
    import sqlite3

    conn = sqlite3.connect(CSA_DB)
    migrate(conn)
    n = 0
    for bdir in sorted(glob.glob(os.path.join(RESULTS, "batch_*"))):
        f = os.path.join(bdir, "accounts.txt")
        if not os.path.exists(f):
            continue
        batch = os.path.basename(bdir)
        for line in open(f, encoding="utf-8", errors="replace"):
            p = line.strip().split("|")
            if len(p) < 1 or not p[0]:
                continue
            at = p[2] if len(p) > 2 else ""
            try:
                from token_util import jwt_exp_unix

                exp = jwt_exp_unix(at)
                health = token_health(at)
                # Fail-closed: dead tokens never enter inject queue as farmed
                if (
                    not at
                    or not str(at).startswith("eyJ")
                    or health in ("expired", "invalid", "missing")
                ):
                    status = "error"
                    notes = "token_expired" if health == "expired" else "bad_token"
                else:
                    status = "farmed"
                    notes = None
                conn.execute(
                    """INSERT INTO accounts
                       (email,password,access_token,refresh_token,batch_id,status,token_exp,token_health,notes)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (
                        p[0],
                        p[1] if len(p) > 1 else "",
                        at,
                        p[3] if len(p) > 3 else "",
                        batch,
                        status,
                        exp,
                        health,
                        notes,
                    ),
                )
                n += 1
            except sqlite3.IntegrityError:
                pass
    conn.commit()
    conn.close()
    harden_db_file(CSA_DB)
    if n:
        print(f"[WORKFLOW] Imported {n} new accounts")
    return n


def fetch_all_proxies_from_9router() -> list[str]:
    """Get ALL proxy URLs from 9router proxyPools via /root/list_proxies.py (source of truth)."""
    r = subprocess.run(
        SSH + ["python3", "/root/list_proxies.py"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    urls: list[str] = []
    for line in (r.stdout or "").splitlines():
        line = line.strip()
        if line.startswith("http"):
            urls.append(line.rstrip("/"))
    seen: set[str] = set()
    out: list[str] = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _proxy_score_map(conn: "sqlite3.Connection") -> dict[str, float]:
    """proxy_key -> score from proxy_stats (default 0.5 if unknown)."""
    out: dict[str, float] = {}
    try:
        for r in conn.execute("SELECT proxy_key, score FROM proxy_stats"):
            try:
                k = str(r[0] if not hasattr(r, "keys") else r["proxy_key"])
                s = float(r[1] if not hasattr(r, "keys") else r["score"] or 0.5)
                out[k] = max(0.01, min(1.0, s))
            except (TypeError, ValueError, KeyError, IndexError):
                continue
    except Exception:
        pass
    return out


def pick_proxy(proxies: list[str], skip_keys: set[str], scores: dict[str, float]) -> str:
    """Score-weighted pick among non-skipped proxies; fail-open to full pool if all skipped."""
    if not proxies:
        raise ValueError("empty proxy list")
    eligible = [p for p in proxies if proxy_key(p) not in skip_keys]
    if not eligible:
        # fail-open: never empty the inject pool via soft skip alone
        eligible = list(proxies)
    weights = [scores.get(proxy_key(p), 0.5) for p in eligible]
    # random.choices needs positive weights
    weights = [max(0.01, float(w)) for w in weights]
    return random.choices(eligible, weights=weights, k=1)[0]


def main() -> int:
    import sqlite3

    print(f"[WORKFLOW] Started {now_iso()}")
    import_batches()

    proxies = fetch_all_proxies_from_9router()
    if not proxies:
        msg = "9router proxyPools empty — abort inject (no local proxy file fallback)"
        print(f"[WORKFLOW] FATAL: {msg}")
        _notify_inject_event(
            "inject aborted",
            msg,
            level="critical",
            extra={"ok": 0, "fail": 1},
        )
        return 1
    print(f"[WORKFLOW] Loaded {len(proxies)} proxies from 9router proxyPools")

    conn = sqlite3.connect(CSA_DB)
    conn.row_factory = sqlite3.Row
    migrate(conn)
    skip_keys: set[str] = set()
    try:
        skip_keys = proxies_to_skip(conn)
    except Exception:
        skip_keys = set()
    scores = _proxy_score_map(conn)
    if skip_keys:
        print(
            f"[WORKFLOW] soft-skip {len(skip_keys)} proxy keys "
            f"(consec/disabled); fail-open if all filtered"
        )
    rows = conn.execute(
        "SELECT email, access_token, refresh_token FROM accounts WHERE status='farmed' ORDER BY id"
    ).fetchall()
    if not rows:
        conn.close()
        print("[WORKFLOW] No pending farmed accounts")
        return 0
    print(f"[WORKFLOW] Pending: {len(rows)}")

    lines: list[str] = []
    email_proxy: dict[str, str] = {}
    skipped_bad = 0
    for row in rows:
        email = row["email"] if isinstance(row, sqlite3.Row) else row[0]
        at = row["access_token"] if isinstance(row, sqlite3.Row) else row[1]
        rt = row["refresh_token"] if isinstance(row, sqlite3.Row) else row[2]
        if not at or not str(at).startswith("eyJ"):
            print(f"  skip {email}: bad token")
            # leave as farmed? mark error so we do not loop forever
            try:
                conn.execute(
                    "UPDATE accounts SET status='error', notes=? WHERE email=? AND status='farmed'",
                    ("bad_token", email),
                )
                conn.commit()
            except Exception:
                pass
            skipped_bad += 1
            continue
        health = token_health(at)
        if health == "expired":
            print(f"  skip {email}: token expired")
            try:
                update_account_token_meta(conn, email, at)
                conn.execute(
                    "UPDATE accounts SET status='error', notes=? WHERE email=? AND status='farmed'",
                    ("token_expired", email),
                )
                conn.commit()
            except Exception as e:
                safe_print(f"[WORKFLOW] mark expired failed {email}: {e}")
            skipped_bad += 1
            continue
        proxy = pick_proxy(proxies, skip_keys, scores)
        email_proxy[email] = proxy
        lines.append(
            json.dumps(
                {
                    "email": email,
                    "access_token": at,
                    "refresh_token": rt or "",
                    "proxy": proxy,
                }
            )
        )
        print(f"  assign {email} -> {redact_proxy_url(proxy)} (token={health})")

    conn.close()

    if not lines:
        print("[WORKFLOW] Nothing to inject")
        if skipped_bad:
            _notify_inject_event(
                "inject empty",
                f"skipped_bad={skipped_bad}",
                level="warning",
                extra={"ok": 0, "fail": skipped_bad},
            )
        return 0

    data = "\n".join(lines) + "\n"
    try:
        r = subprocess.run(
            SSH + ["python3", "/root/grok_cli_bulk_inject.py"],
            input=data,
            capture_output=True,
            text=True,
            timeout=600,
        )
        # stdout may contain emails only (OK lines) — safe; redact any accidental token dumps
        if r.stdout:
            print(redact(r.stdout))
        if r.stderr:
            print(redact(r.stderr[:500]))
        if r.returncode != 0 and not (r.stdout or "").strip():
            raise RuntimeError(
                f"inject remote exit={r.returncode} stderr={redact((r.stderr or '')[:300])}"
            )
        ok_emails: set[str] = set()
        for line in (r.stdout or "").splitlines():
            if line.startswith("OK "):
                ok_emails.add(line[3:].strip())
        attempted = set(email_proxy.keys())
        failed = attempted - ok_emails
        ts = now_iso()
        c2 = sqlite3.connect(CSA_DB)
        c2.row_factory = sqlite3.Row
        migrate(c2)
        # 1) Commit inject marks first — must not roll back if proxy_stats fails
        for email in ok_emails:
            proxy = email_proxy.get(email, "")
            c2.execute(
                "UPDATE accounts SET status='injected', injected_at=?, proxy_used=? "
                "WHERE email=? AND status='farmed'",
                (ts, proxy, email),
            )
            row = c2.execute(
                "SELECT access_token FROM accounts WHERE email=?", (email,)
            ).fetchone()
            tok = row["access_token"] if row is not None else None
            if tok:
                update_account_token_meta(c2, email, tok)
        c2.commit()
        # 2) Best-effort proxy scoring + fail taxonomy (never undo inject marks)
        fail_class = ""
        try:
            from db_schema import classify_proxy_fail

            err_blob = f"{r.stderr or ''}\n{r.stdout or ''}"
            fail_class = classify_proxy_fail(err_blob) if failed else ""
            for email in ok_emails:
                proxy = email_proxy.get(email, "")
                record_proxy_result(c2, proxy, True, email=email, when_iso=ts)
            for email in failed:
                proxy = email_proxy.get(email, "")
                record_proxy_result(
                    c2,
                    proxy,
                    False,
                    email=email,
                    when_iso=ts,
                    fail_reason=fail_class or "unknown",
                )
            c2.commit()
        except Exception as pe:
            safe_print(f"[WORKFLOW] proxy_stats update error (inject marks kept): {pe}")
        c2.close()
        harden_db_file(CSA_DB)
        print(
            f"[WORKFLOW] Marked {len(ok_emails)} as injected "
            f"(failed={len(failed)}, proxy from 9router pools)"
        )
        # Per-batch inject alert with full email lists (no JWT/password).
        try:
            import socket

            from alerts import format_email_list

            host = socket.gethostname()
            ok_list = sorted(ok_emails)
            fail_list = sorted(failed)
            fail_detail_lines: list[str] = []
            for em in fail_list[:40]:
                px = redact_proxy_url(email_proxy.get(em, "") or "")
                fail_detail_lines.append(f"  • {em} proxy={px or '-'} class={fail_class or 'unknown'}")
            if len(fail_list) > 40:
                fail_detail_lines.append(f"  … +{len(fail_list) - 40} more")

            body_parts = [
                f"host={host}",
                f"ok={len(ok_list)} failed={len(fail_list)} attempted={len(attempted)}",
                f"fail_class={fail_class or '-'}",
                "",
                f"OK injected ({len(ok_list)}):",
                format_email_list(ok_list),
            ]
            if fail_list:
                body_parts.append("")
                body_parts.append(f"FAILED inject ({len(fail_list)}):")
                body_parts.extend(fail_detail_lines if fail_detail_lines else [format_email_list(fail_list)])

            body = "\n".join(body_parts)
            if failed and not ok_emails:
                _notify_inject_event(
                    "inject all failed",
                    body,
                    level="critical",
                    extra={
                        "host": host,
                        "ok": 0,
                        "fail": len(fail_list),
                        "failed": len(fail_list),
                        "fail_class": fail_class,
                    },
                    ok_emails=[],
                    fail_emails=fail_list,
                    fail_class=fail_class or "",
                )
            elif failed:
                _notify_inject_event(
                    "inject partial",
                    body,
                    level="warning",
                    extra={
                        "host": host,
                        "ok": len(ok_list),
                        "fail": len(fail_list),
                        "failed": len(fail_list),
                        "fail_class": fail_class or "",
                    },
                    ok_emails=ok_list,
                    fail_emails=fail_list,
                    fail_class=fail_class or "",
                )
            elif ok_emails:
                _notify_inject_event(
                    "inject batch ok",
                    body,
                    level="success",
                    extra={
                        "host": host,
                        "ok": len(ok_list),
                        "fail": 0,
                        "created": len(ok_list),
                    },
                    ok_emails=ok_list,
                    fail_emails=[],
                )
        except Exception as ae:
            safe_print(f"[WORKFLOW] inject alert error: {ae}")
    except Exception as e:
        safe_print(f"[WORKFLOW] inject error: {e}")
        _notify_inject_event(
            "inject error",
            str(e),
            level="critical",
            extra={"ok": 0, "fail": 1},
        )
        return 1
    print(f"[WORKFLOW] Finished {now_iso()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
