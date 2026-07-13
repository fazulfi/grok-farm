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
    ensure_probe_columns,
    ensure_token_columns,
    mark_expired_accounts,
    probe_accounts,
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
RUN_PROBE = "--probe" in sys.argv


def _probe_limit_from_argv() -> int:
    """--probe-limit N or GROK_PROBE_LIMIT or default 20 when --probe."""
    argv = sys.argv[1:]
    for i, a in enumerate(argv):
        if a == "--probe-limit" and i + 1 < len(argv):
            try:
                return max(1, int(argv[i + 1]))
            except ValueError:
                break
        if a.startswith("--probe-limit="):
            try:
                return max(1, int(a.split("=", 1)[1]))
            except ValueError:
                break
    raw = (os.environ.get("GROK_PROBE_LIMIT") or "").strip()
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            pass
    return 20


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
    ensure_probe_columns(conn)

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

    probe_stats = None
    if RUN_PROBE:
        probe_stats = probe_accounts(
            conn,
            statuses=("injected",),
            limit=_probe_limit_from_argv(),
            dry_run=False,
            mark=True,
            skip_expired=True,
            mark_error=False,
        )

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

    # Live probe inventory (soft): needs_relogin flag + last_probe_status breakdown
    needs_relogin_count = 0
    probe_status_breakdown: dict[str, int] = {}
    probed_count = 0
    unprobed_count = 0
    proxy_skip_n = 0
    proxy_disabled_n = 0
    try:
        needs_relogin_count = int(
            conn.execute(
                "SELECT COUNT(*) FROM accounts WHERE needs_relogin=1"
            ).fetchone()[0]
            or 0
        )
        for r in conn.execute(
            """SELECT last_probe_status, COUNT(*) FROM accounts
               WHERE last_probe_status IS NOT NULL AND last_probe_status != ''
               GROUP BY last_probe_status"""
        ).fetchall():
            key, cnt = r[0], r[1]
            if key:
                probe_status_breakdown[str(key)] = int(cnt)
        probed_count = int(
            conn.execute(
                """SELECT COUNT(*) FROM accounts
                   WHERE last_probe_status IS NOT NULL AND last_probe_status != ''"""
            ).fetchone()[0]
            or 0
        )
        unprobed_count = max(0, int(total or 0) - probed_count)
    except sqlite3.Error:
        pass
    try:
        from db_schema import proxies_to_skip

        proxy_skip_n = len(proxies_to_skip(conn))
        proxy_disabled_n = int(
            conn.execute(
                "SELECT COUNT(*) FROM proxy_stats WHERE disabled=1"
            ).fetchone()[0]
            or 0
        )
    except Exception:
        proxy_skip_n = 0
        proxy_disabled_n = 0
    conn.close()

    id_cfg = identity_report()
    configured = set(id_cfg.get("domains") or [])
    inventory_only = sorted(set(inv_domains) - configured) if configured else []

    def _pct(n: int, den: int) -> float:
        if not den:
            return 0.0
        return round(100.0 * float(n) / float(den), 1)

    # Soft policy dashboard: offline JWT + live probe inventory (never hard-flips)
    th_ok = int(token_counts.get("ok") or 0)
    th_soon = int(token_counts.get("expiring_soon") or 0)
    th_exp = int(token_counts.get("expired") or 0)
    th_inv = int(token_counts.get("invalid") or 0)
    th_miss = int(token_counts.get("missing") or 0)
    alive_n = int(probe_status_breakdown.get("alive") or 0)
    jwt_exp_probe = int(probe_status_breakdown.get("jwt_expired") or 0)
    soft_policy = {
        "policy": "soft",
        "note": "meta only; no default injected→error",
        "total": int(total or 0),
        "jwt_offline": {
            "ok": th_ok,
            "expiring_soon": th_soon,
            "expired": th_exp,
            "invalid": th_inv,
            "missing": th_miss,
            "ok_pct": _pct(th_ok, int(total or 0)),
            "expired_pct": _pct(th_exp, int(total or 0)),
            "healthy_pct": _pct(th_ok + th_soon, int(total or 0)),
        },
        "live_probe": {
            "probed": probed_count,
            "unprobed": unprobed_count,
            "probed_pct": _pct(probed_count, int(total or 0)),
            "needs_relogin": needs_relogin_count,
            "needs_relogin_pct": _pct(needs_relogin_count, int(total or 0)),
            "alive": alive_n,
            "alive_pct_of_probed": _pct(alive_n, probed_count),
            "jwt_expired_probe": jwt_exp_probe,
            "by_status": dict(probe_status_breakdown),
        },
    }

    report["accounts"] = {
        "total": total,
        "by_status": by_status,
        "farmed": by_status.get("farmed", 0),
        "injected": by_status.get("injected", 0),
        "error": by_status.get("error", 0),
    }
    report["token_health"] = token_counts
    report["soft_policy"] = soft_policy
    report["probe"] = {
        "needs_relogin": needs_relogin_count,
        "last_probe_status": probe_status_breakdown,
        "probed": probed_count,
        "unprobed": unprobed_count,
    }
    if probe_stats is not None:
        report["probe_run"] = probe_stats
    report["proxy_stats_top"] = top_proxies
    report["domain_stats"] = domain_health
    report["recent"] = recent
    report["identity"] = id_cfg
    report["domains_inventory"] = inv_domains
    report["domains_not_in_pool"] = inventory_only
    report["farmed_expired"] = int(farmed_expired or 0)
    if mark_stats is not None:
        report["mark_expired"] = mark_stats

    # overall health: hard issues → exit 3 (webhook); soft → exit 2 (log only)
    issues_hard: list[str] = []
    issues_soft: list[str] = []
    if report["farmer"] != "active":
        issues_hard.append("farmer_not_active")
    if report["accounts"]["farmed"] > 50:
        issues_hard.append("large_farmed_backlog")
    if report["farmed_expired"] > 0:
        issues_hard.append("farmed_expired_tokens")
    if token_counts.get("expired", 0) > 50:
        # soft inventory signal only — does not fail inject of healthy farmed
        issues_soft.append("many_expired_tokens")
    if needs_relogin_count > 0:
        # soft inventory — re-auth needed; does not stop farmer
        issues_soft.append("needs_relogin")
    if report["backup"].get("status") in ("stale", "no_log"):
        issues_hard.append("backup_" + str(report["backup"].get("status")))
    disk = report.get("disk") or {}
    if isinstance(disk.get("used_pct"), (int, float)) and disk["used_pct"] >= 90:
        issues_hard.append("disk_high")
    if id_cfg.get("error"):
        issues_hard.append("identity_config_error")
    elif id_cfg.get("mode") == "domain" and not (id_cfg.get("domains") or []):
        issues_hard.append("no_email_domains")
    # proxy pool quality (local cache file; inject uses live proxyPools)
    pfc = int(report.get("proxy_file_count") or 0)
    if pfc <= 0:
        issues_hard.append("proxy_file_empty")
    elif pfc < 3:
        issues_soft.append("proxy_pool_low")
    report["proxy_soft_skip"] = {
        "skip_keys": proxy_skip_n,
        "disabled": proxy_disabled_n,
    }
    if proxy_skip_n > 0 and pfc > 0 and proxy_skip_n >= max(1, pfc // 2):
        issues_soft.append("proxy_many_soft_skipped")
    issues = issues_hard + issues_soft
    report["issues"] = issues
    report["issues_hard"] = issues_hard
    report["issues_soft"] = issues_soft
    report["healthy"] = len(issues) == 0
    # exit: 0 ok | 2 soft-only | 3 hard (alert path)
    if issues_hard:
        exit_code = 3
    elif issues_soft:
        exit_code = 2
    else:
        exit_code = 0
    report["exit_code"] = exit_code

    if WANT_JSON:
        print(json.dumps(report, indent=2))
        return exit_code

    a = report["accounts"]
    print(f"=== Grok Farm Health @ {report['ts']} ===")
    print(f"farmer:     {report['farmer']}")
    print(
        f"accounts:   total={a['total']} injected={a['injected']} "
        f"farmed={a['farmed']} error={a['error']}"
    )
    print(f"tokens:     {token_counts}")
    sp = report.get("soft_policy") or {}
    jo = sp.get("jwt_offline") or {}
    lp = sp.get("live_probe") or {}
    print(
        f"soft_policy: jwt ok={jo.get('ok_pct')}% expired={jo.get('expired_pct')}% "
        f"healthy={jo.get('healthy_pct')}% | "
        f"probe covered={lp.get('probed_pct')}% "
        f"needs_relogin={lp.get('needs_relogin')} ({lp.get('needs_relogin_pct')}%) "
        f"alive={lp.get('alive')} ({lp.get('alive_pct_of_probed')}% of probed) "
        f"unprobed={lp.get('unprobed')}"
    )
    print(
        f"probe:      needs_relogin={needs_relogin_count} "
        f"probed={probed_count} unprobed={unprobed_count} "
        f"last_status={probe_status_breakdown or {}}"
    )
    if probe_stats is not None:
        by = probe_stats.get("by_status") or {}
        print(
            f"probe_run:  scanned={probe_stats.get('scanned')} "
            f"alive={by.get('alive', 0)} needs_relogin={by.get('needs_relogin', 0)} "
            f"jwt_expired={by.get('jwt_expired', 0)} "
            f"network_error={by.get('network_error', 0)}"
        )
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
    if issues_hard or issues_soft:
        if issues_hard:
            print("ISSUES_HARD:", ", ".join(issues_hard))
        if issues_soft:
            print("ISSUES_SOFT:", ", ".join(issues_soft))
        return exit_code
    print("OK healthy")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
