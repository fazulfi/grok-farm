#!/usr/bin/env python3
"""Health CLI: inventory, farmer service, disk, proxies, tokens, backup, proxy scores."""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from db_schema import DEFAULT_DB, migrate
from email_identity import domain_counts_from_emails, load_identity_pool
from log_redact import redact_proxy_url
from token_util import (
    ensure_token_columns,
    mark_expired_accounts,
    token_health,
    update_account_token_meta,
)

FARM = Path(os.path.expanduser(os.environ.get("GROK_FARM_DIR") or "~/grok-farm"))
DB = Path(os.path.expanduser(os.environ.get("GROK_AKUN_DB") or str(FARM / "akun.db")))
PROXY_FILE = FARM / "usa_proxies.txt"
BACKUP_LOG = FARM / "logs" / "s3_backup.log"
WANT_JSON = "--json" in sys.argv
MARK_EXPIRED = "--mark-expired" in sys.argv
MARK_EXPIRED_INJECTED = "--mark-expired-injected" in sys.argv


def identity_report() -> dict:
    """Configured domain/IMAP pool (no secrets)."""
    try:
        # load .env if present so pool matches farmer
        env_path = FARM / ".env"
        if env_path.is_file():
            try:
                from dotenv import load_dotenv

                load_dotenv(env_path, override=False)
            except ImportError:
                for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, _, v = line.partition("=")
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
        pool = load_identity_pool(FARM)
        return pool.summary()
    except Exception as e:
        return {"error": str(e)}


def _run(cmd: list[str], timeout: int = 10) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return (r.stdout or r.stderr or "").strip()
    except Exception as e:
        return f"error: {e}"


def farmer_active() -> str:
    out = _run(["systemctl", "is-active", "grok-farmer"])
    return out or "unknown"


def disk_info(path: Path) -> dict:
    try:
        u = shutil.disk_usage(str(path))
        return {
            "total_gb": round(u.total / (1024**3), 2),
            "used_gb": round(u.used / (1024**3), 2),
            "free_gb": round(u.free / (1024**3), 2),
            "used_pct": round(100.0 * u.used / u.total, 1) if u.total else 0,
        }
    except OSError as e:
        return {"error": str(e)}


def proxy_file_count() -> int:
    """Count usable proxy lines (URL or host:port[:user:pass])."""
    if not PROXY_FILE.is_file():
        return 0
    n = 0
    for line in PROXY_FILE.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # URL form or colon-separated host:port...
        if "://" in line or (":" in line and not line.startswith("[")):
            n += 1
    return n


def last_backup_info() -> dict:
    info: dict = {"log": str(BACKUP_LOG), "last_ok": None, "last_line": None}
    if not BACKUP_LOG.is_file():
        info["status"] = "no_log"
        return info
    try:
        lines = BACKUP_LOG.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as e:
        info["status"] = f"read_error: {e}"
        return info
    last_ok = None
    for line in reversed(lines[-200:]):
        if "backup done" in line.lower() or "Upload OK" in line:
            last_ok = line.strip()
            break
    info["last_ok"] = last_ok
    info["last_line"] = lines[-1].strip() if lines else None
    info["status"] = "ok" if last_ok else "unknown"
    # mtime
    try:
        mtime = BACKUP_LOG.stat().st_mtime
        age_h = (time.time() - mtime) / 3600.0
        info["log_age_hours"] = round(age_h, 2)
        if age_h > 3:
            info["status"] = "stale"
    except OSError:
        pass
    return info


def refresh_token_health(conn: sqlite3.Connection) -> dict:
    ensure_token_columns(conn)
    rows = conn.execute(
        "SELECT email, access_token FROM accounts WHERE access_token IS NOT NULL AND access_token != ''"
    ).fetchall()
    counts = {"ok": 0, "expiring_soon": 0, "expired": 0, "invalid": 0, "missing": 0}
    for email, at in rows:
        h = update_account_token_meta(conn, email, at or "")
        counts[h] = counts.get(h, 0) + 1
    conn.commit()
    return counts


def main() -> int:
    report: dict = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "farm_dir": str(FARM),
        "db": str(DB),
        "farmer": farmer_active(),
        "disk": disk_info(FARM if FARM.exists() else Path.home()),
        "proxy_file_count": proxy_file_count(),
        "backup": last_backup_info(),
    }

    if not DB.is_file():
        report["error"] = "akun.db missing"
        if WANT_JSON:
            print(json.dumps(report, indent=2))
        else:
            print("ERROR: akun.db missing at", DB)
            print("farmer:", report["farmer"])
        return 1

    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row
    migrate(conn)

    total = conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
    by_status = {
        r[0]: r[1]
        for r in conn.execute(
            "SELECT status, COUNT(*) FROM accounts GROUP BY status"
        ).fetchall()
    }
    token_counts = refresh_token_health(conn)

    mark_stats = None
    if MARK_EXPIRED or MARK_EXPIRED_INJECTED:
        mark_stats = mark_expired_accounts(
            conn,
            include_injected=MARK_EXPIRED_INJECTED,
            dry_run=False,
        )
        # re-count statuses after marks
        by_status = {
            r[0]: r[1]
            for r in conn.execute(
                "SELECT status, COUNT(*) FROM accounts GROUP BY status"
            ).fetchall()
        }
        token_counts = refresh_token_health(conn)

    top_proxies = []
    try:
        for r in conn.execute(
            """SELECT proxy_key, success_count, fail_count, score
               FROM proxy_stats ORDER BY score DESC, success_count DESC LIMIT 5"""
        ):
            top_proxies.append(
                {
                    "proxy": redact_proxy_url(r[0], keep=60),
                    "ok": r[1],
                    "fail": r[2],
                    "score": round(float(r[3]), 3),
                }
            )
    except sqlite3.Error:
        pass

    domain_health = []
    try:
        for r in conn.execute(
            """SELECT domain, success_count, fail_count, consecutive_fails,
                      score, disabled FROM domain_stats
               ORDER BY consecutive_fails DESC, fail_count DESC LIMIT 10"""
        ):
            domain_health.append(
                {
                    "domain": r[0],
                    "ok": r[1],
                    "fail": r[2],
                    "consecutive_fails": r[3],
                    "score": round(float(r[4] or 0), 3),
                    "disabled": bool(r[5]),
                }
            )
    except sqlite3.Error:
        pass

    recent = []
    for r in conn.execute(
        "SELECT id,email,status,batch_id,token_health FROM accounts ORDER BY id DESC LIMIT 5"
    ):
        recent.append(
            {
                "id": r[0],
                "email": r[1],
                "status": r[2],
                "batch_id": r[3],
                "token_health": r[4],
            }
        )

    all_emails = [r[0] for r in conn.execute("SELECT email FROM accounts").fetchall()]
    inv_domains = domain_counts_from_emails(all_emails)
    # Hard issue only if pending farmed tokens are already dead (inject will skip).
    # Historical injected JWT expiry is inventory age, not pipeline failure.
    farmed_expired = conn.execute(
        """SELECT COUNT(*) FROM accounts
           WHERE status='farmed' AND token_health='expired'"""
    ).fetchone()[0]
    conn.close()

    id_cfg = identity_report()
    configured = set(id_cfg.get("domains") or [])
    inventory_only = sorted(set(inv_domains) - configured) if configured else []

    report["accounts"] = {
        "total": total,
        "by_status": by_status,
        "farmed": by_status.get("farmed", 0),
        "injected": by_status.get("injected", 0),
        "error": by_status.get("error", 0),
    }
    report["token_health"] = token_counts
    report["proxy_stats_top"] = top_proxies
    report["domain_stats"] = domain_health
    report["recent"] = recent
    report["identity"] = id_cfg
    report["domains_inventory"] = inv_domains
    report["domains_not_in_pool"] = inventory_only
    report["farmed_expired"] = int(farmed_expired or 0)
    if mark_stats is not None:
        report["mark_expired"] = mark_stats

    # overall health flag
    issues = []
    if report["farmer"] != "active":
        issues.append("farmer_not_active")
    if report["accounts"]["farmed"] > 50:
        issues.append("large_farmed_backlog")
    if report["farmed_expired"] > 0:
        issues.append("farmed_expired_tokens")
    elif token_counts.get("expired", 0) > 50:
        # soft inventory signal only — does not fail inject of healthy farmed
        issues.append("many_expired_tokens")
    if report["backup"].get("status") in ("stale", "no_log"):
        issues.append("backup_" + str(report["backup"].get("status")))
    disk = report.get("disk") or {}
    if isinstance(disk.get("used_pct"), (int, float)) and disk["used_pct"] >= 90:
        issues.append("disk_high")
    if id_cfg.get("error"):
        issues.append("identity_config_error")
    elif id_cfg.get("mode") == "domain" and not (id_cfg.get("domains") or []):
        issues.append("no_email_domains")
    report["issues"] = issues
    report["healthy"] = len(issues) == 0

    if WANT_JSON:
        print(json.dumps(report, indent=2))
        return 0 if report["healthy"] else 2

    a = report["accounts"]
    print(f"=== Grok Farm Health @ {report['ts']} ===")
    print(f"farmer:     {report['farmer']}")
    print(
        f"accounts:   total={a['total']} injected={a['injected']} "
        f"farmed={a['farmed']} error={a['error']}"
    )
    print(f"tokens:     {token_counts}")
    if mark_stats is not None:
        print(
            f"mark_exp:   marked_error={mark_stats.get('marked_error')} "
            f"farmed_dead={mark_stats.get('farmed_expired')} "
            f"injected_expired={mark_stats.get('injected_expired')}"
        )
    print(f"proxies:    file_lines={report['proxy_file_count']}")
    idn = report.get("identity") or {}
    if idn.get("error"):
        print(f"identity:   ERROR {idn['error']}")
    else:
        print(
            f"identity:   mode={idn.get('mode')} domains={idn.get('domain_count')} "
            f"ids={idn.get('identity_count')} strategy={idn.get('strategy')} "
            f"src={idn.get('source')}"
        )
        if idn.get("domains"):
            print(f"  pool:     {', '.join(idn['domains'])}")
    if inv_domains:
        top_d = list(inv_domains.items())[:8]
        print(
            "domains:    inventory "
            + ", ".join(f"{d}={n}" for d, n in top_d)
            + (" …" if len(inv_domains) > 8 else "")
        )
    d = report["disk"]
    if "free_gb" in d:
        print(f"disk:       free={d['free_gb']}G used={d['used_pct']}%")
    else:
        print(f"disk:       {d}")
    b = report["backup"]
    print(f"backup:     status={b.get('status')} age_h={b.get('log_age_hours')} last={b.get('last_ok')}")
    if top_proxies:
        print("proxy_top:")
        for p in top_proxies:
            print(f"  score={p['score']} ok={p['ok']} fail={p['fail']} {p['proxy']}")
    if domain_health:
        print("domain_stats:")
        for drow in domain_health:
            flag = " DISABLED" if drow.get("disabled") else ""
            print(
                f"  {drow['domain']}: score={drow['score']} ok={drow['ok']} "
                f"fail={drow['fail']} consec={drow['consecutive_fails']}{flag}"
            )
    print("recent:")
    for r in recent:
        print(
            f"  [{r['id']}] {r['email']:45s} {r['status']:10s} "
            f"tok={r.get('token_health') or '-'} ({r['batch_id']})"
        )
    if issues:
        print("ISSUES:", ", ".join(issues))
        return 2
    print("OK healthy")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
