#!/usr/bin/env python3
"""Daily fleet digest + proxy dashboard → Telegram (HTML card).

Per-host report (each farmer posts its own card with hostname). Operator
receives one message per host ≈ once/day = fleet-wide view in the bot.

Usage:
  python daily_digest.py              # full digest (health + proxy)
  python daily_digest.py --proxy-only # proxy dashboard only
  python daily_digest.py --dry-run    # print body, no Telegram
  python daily_digest.py --json       # machine-readable payload

Never includes JWT/password/proxy user:pass (redacted).
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sqlite3
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

FARM = Path(os.path.expanduser(os.environ.get("GROK_FARM_DIR") or "~/grok-farm"))
DB = Path(os.path.expanduser(os.environ.get("GROK_AKUN_DB") or str(FARM / "akun.db")))
PROXY_FILE = FARM / "usa_proxies.txt"

# Ensure farm root on path when invoked as scripts/daily_digest.sh → python daily_digest.py
_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
if str(FARM) not in sys.path:
    sys.path.insert(0, str(FARM))

from db_schema import migrate, proxies_to_skip, proxy_max_consecutive_fails  # noqa: E402
from log_redact import redact_proxy_url  # noqa: E402


def _hostname() -> str:
    return (
        (os.environ.get("GROK_HOST_TAG") or "").strip()
        or socket.gethostname().split(".")[0]
        or "farm"
    )


def _load_env() -> None:
    env_path = FARM / ".env"
    if not env_path.is_file():
        return
    try:
        from dotenv import load_dotenv

        load_dotenv(env_path, override=False)
        return
    except ImportError:
        pass
    try:
        for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    except OSError:
        pass


def _py() -> str:
    venv = FARM / ".venv" / "bin" / "python"
    if venv.is_file():
        return str(venv)
    return sys.executable


def run_check_status_json() -> tuple[Optional[dict[str, Any]], int]:
    """Invoke check_status --json for inventory/health (no probe/mark)."""
    cmd = [_py(), str(FARM / "check_status.py"), "--json"]
    try:
        r = subprocess.run(
            cmd,
            cwd=str(FARM),
            capture_output=True,
            text=True,
            timeout=120,
            env=os.environ.copy(),
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"error": str(e)}, 1
    raw = (r.stdout or "").strip()
    if not raw:
        return {"error": "empty check_status stdout", "stderr": (r.stderr or "")[:200]}, r.returncode
    try:
        return json.loads(raw), r.returncode
    except json.JSONDecodeError as e:
        return {"error": f"json: {e}", "raw_head": raw[:200]}, r.returncode


def proxy_file_count() -> int:
    if not PROXY_FILE.is_file():
        return 0
    n = 0
    for line in PROXY_FILE.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "://" in line or (":" in line and not line.startswith("[")):
            n += 1
    return n


def build_proxy_dashboard(conn: sqlite3.Connection) -> dict[str, Any]:
    """Proxy score / soft-skip / fail taxonomy dashboard (redacted keys)."""
    thr = proxy_max_consecutive_fails()
    skip = proxies_to_skip(conn)
    pfc = proxy_file_count()
    rows: list[dict[str, Any]] = []
    reason_counts: Counter[str] = Counter()
    try:
        for r in conn.execute(
            """SELECT proxy_key, success_count, fail_count, consecutive_fails,
                      score, disabled, last_fail_reason, last_success_at, last_fail_at
               FROM proxy_stats
               ORDER BY score ASC, fail_count DESC"""
        ):
            key = str(r[0] or "")
            sc, fc = int(r[1] or 0), int(r[2] or 0)
            cf = int(r[3] or 0)
            score = round(float(r[4] or 0), 3)
            disabled = int(r[5] or 0)
            reason = (r[6] or "") or ""
            if reason:
                reason_counts[str(reason)] += 1
            rows.append(
                {
                    "proxy": redact_proxy_url(key, keep=48),
                    "proxy_key_short": key[:40] + ("…" if len(key) > 40 else ""),
                    "ok": sc,
                    "fail": fc,
                    "consecutive_fails": cf,
                    "score": score,
                    "disabled": bool(disabled),
                    "soft_skip": key in skip or bool(disabled) or cf >= thr,
                    "last_fail_reason": reason or None,
                    "last_success_at": r[7],
                    "last_fail_at": r[8],
                }
            )
    except sqlite3.Error as e:
        return {
            "error": str(e),
            "proxy_file_count": pfc,
            "tracked": 0,
            "soft_skip": 0,
            "disabled": 0,
            "threshold_consecutive": thr,
            "fail_reasons": {},
            "top": [],
            "worst": [],
        }

    disabled_n = sum(1 for x in rows if x["disabled"])
    soft_n = sum(1 for x in rows if x["soft_skip"])
    # top = best score (desc); worst = lowest score with fails
    by_score_desc = sorted(rows, key=lambda x: (-x["score"], -x["ok"]))
    by_score_asc = sorted(
        [x for x in rows if x["fail"] > 0 or x["soft_skip"]],
        key=lambda x: (x["score"], -x["fail"], -x["consecutive_fails"]),
    )
    return {
        "proxy_file_count": pfc,
        "tracked": len(rows),
        "soft_skip": soft_n,
        "disabled": disabled_n,
        "threshold_consecutive": thr,
        "fail_reasons": dict(reason_counts.most_common(12)),
        "top": by_score_desc[:8],
        "worst": by_score_asc[:8],
    }


def build_domain_snapshot(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    try:
        for r in conn.execute(
            """SELECT domain, success_count, fail_count, consecutive_fails,
                      score, disabled FROM domain_stats
               ORDER BY consecutive_fails DESC, fail_count DESC LIMIT 8"""
        ):
            out.append(
                {
                    "domain": r[0],
                    "ok": int(r[1] or 0),
                    "fail": int(r[2] or 0),
                    "consecutive_fails": int(r[3] or 0),
                    "score": round(float(r[4] or 0), 3),
                    "disabled": bool(r[5]),
                }
            )
    except sqlite3.Error:
        pass
    return out


def _fmt_proxy_line(p: dict[str, Any]) -> str:
    flag = ""
    if p.get("disabled"):
        flag = " ⛔"
    elif p.get("soft_skip"):
        flag = " ⏸"
    reason = p.get("last_fail_reason") or ""
    rpart = f" [{reason}]" if reason else ""
    return (
        f"  • score={p['score']} ok={p['ok']} fail={p['fail']} "
        f"cf={p['consecutive_fails']}{flag} {p['proxy']}{rpart}"
    )


def format_digest_body(
    host: str,
    health: Optional[dict[str, Any]],
    health_rc: int,
    proxy: dict[str, Any],
    domains: list[dict[str, Any]],
    *,
    proxy_only: bool,
) -> tuple[str, str, dict[str, Any]]:
    """Return (title, plain_body, extra) for send_alert."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    if proxy_only:
        title = f"Proxy dashboard · {host}"
    else:
        title = f"Daily digest · {host}"

    lines: list[str] = [f"host={host} ts={ts}"]
    extra: dict[str, Any] = {"host": host}

    if not proxy_only and health is not None:
        if health.get("error"):
            lines.append(f"health_error={health.get('error')}")
        acc = health.get("accounts") or {}
        farmer = health.get("farmer")
        hard = health.get("issues_hard") or []
        soft = health.get("issues_soft") or []
        disk = health.get("disk") or {}
        backup = health.get("backup") or {}
        th = health.get("token_health") or {}
        sp = health.get("soft_policy") or {}
        jo = sp.get("jwt_offline") or {}
        lp = sp.get("live_probe") or {}

        lines.append("HEALTH:")
        lines.append(
            f"farmer={farmer} exit={health_rc} "
            f"accounts total={acc.get('total')} injected={acc.get('injected')} "
            f"farmed={acc.get('farmed')} error={acc.get('error')}"
        )
        lines.append(
            f"tokens ok={th.get('ok')} soon={th.get('expiring_soon')} "
            f"expired={th.get('expired')} invalid={th.get('invalid')}"
        )
        lines.append(
            f"jwt ok_pct={jo.get('ok_pct')}% expired_pct={jo.get('expired_pct')}% | "
            f"probe needs_relogin={lp.get('needs_relogin')} "
            f"alive={lp.get('alive')} unprobed={lp.get('unprobed')}"
        )
        if disk.get("free_gb") is not None:
            lines.append(
                f"disk free={disk.get('free_gb')}G used={disk.get('used_pct')}%"
            )
        lines.append(
            f"backup status={backup.get('status')} age_h={backup.get('log_age_hours')}"
        )
        lines.append(
            f"hard={','.join(hard) if hard else '(none)'} | "
            f"soft={','.join(soft) if soft else '(none)'}"
        )
        extra["ok"] = int(acc.get("injected") or 0)
        extra["fail"] = int(acc.get("error") or 0) + int(acc.get("farmed") or 0)
        extra["issues"] = list(hard) + list(soft)

    # Proxy dashboard section
    lines.append("PROXY DASHBOARD:")
    if proxy.get("error"):
        lines.append(f"proxy_error={proxy['error']}")
    lines.append(
        f"file_lines={proxy.get('proxy_file_count')} tracked={proxy.get('tracked')} "
        f"soft_skip={proxy.get('soft_skip')} disabled={proxy.get('disabled')} "
        f"cf_threshold={proxy.get('threshold_consecutive')}"
    )
    reasons = proxy.get("fail_reasons") or {}
    if reasons:
        rbits = ", ".join(f"{k}={v}" for k, v in list(reasons.items())[:8])
        lines.append(f"fail_reasons: {rbits}")
    top = proxy.get("top") or []
    if top:
        lines.append(f"TOP proxies ({len(top)}):")
        for p in top[:6]:
            lines.append(_fmt_proxy_line(p))
    worst = proxy.get("worst") or []
    if worst:
        lines.append(f"WORST / soft-skip ({len(worst)}):")
        for p in worst[:6]:
            lines.append(_fmt_proxy_line(p))
    if not top and not worst:
        lines.append("  • (no proxy_stats rows yet — inject will populate)")

    if domains and not proxy_only:
        lines.append("DOMAIN STATS:")
        for d in domains[:6]:
            flag = " DISABLED" if d.get("disabled") else ""
            lines.append(
                f"  • {d['domain']}: score={d['score']} ok={d['ok']} "
                f"fail={d['fail']} cf={d['consecutive_fails']}{flag}"
            )

    lines.append("note: soft inventory only — no reauth; no gateway DELETE")
    body = "\n".join(lines)
    return title, body, extra


def pick_level(health: Optional[dict[str, Any]], health_rc: int, proxy: dict[str, Any]) -> str:
    hard = (health or {}).get("issues_hard") or []
    if health_rc == 3 or hard:
        return "warning"
    if health_rc not in (0, 2) and health is not None:
        return "error"
    pfc = int(proxy.get("proxy_file_count") or 0)
    if pfc <= 0:
        return "warning"
    soft_skip = int(proxy.get("soft_skip") or 0)
    if pfc > 0 and soft_skip >= max(1, pfc // 2):
        return "warning"
    soft = (health or {}).get("issues_soft") or []
    if soft or health_rc == 2:
        return "info"
    return "success"


def main() -> int:
    parser = argparse.ArgumentParser(description="Grok Farm daily digest + proxy dashboard")
    parser.add_argument("--proxy-only", action="store_true", help="Proxy dashboard only")
    parser.add_argument("--dry-run", action="store_true", help="Print only, no Telegram")
    parser.add_argument("--json", action="store_true", help="Print JSON payload")
    args = parser.parse_args()

    _load_env()
    host = _hostname()
    health: Optional[dict[str, Any]] = None
    health_rc = 0
    if not args.proxy_only:
        health, health_rc = run_check_status_json()

    proxy: dict[str, Any] = {
        "proxy_file_count": proxy_file_count(),
        "tracked": 0,
        "soft_skip": 0,
        "disabled": 0,
        "threshold_consecutive": proxy_max_consecutive_fails(),
        "fail_reasons": {},
        "top": [],
        "worst": [],
    }
    domains: list[dict[str, Any]] = []
    if DB.is_file():
        try:
            conn = sqlite3.connect(str(DB))
            conn.row_factory = sqlite3.Row
            migrate(conn)
            proxy = build_proxy_dashboard(conn)
            domains = build_domain_snapshot(conn)
            conn.close()
        except sqlite3.Error as e:
            proxy["error"] = str(e)
    else:
        proxy["error"] = "akun.db missing"

    title, body, extra = format_digest_body(
        host, health, health_rc, proxy, domains, proxy_only=args.proxy_only
    )
    level = pick_level(health, health_rc, proxy)
    payload = {
        "title": title,
        "body": body,
        "level": level,
        "extra": extra,
        "health_rc": health_rc,
        "proxy": {
            "file": proxy.get("proxy_file_count"),
            "tracked": proxy.get("tracked"),
            "soft_skip": proxy.get("soft_skip"),
            "disabled": proxy.get("disabled"),
            "fail_reasons": proxy.get("fail_reasons"),
        },
    }

    if args.json:
        print(json.dumps(payload, indent=2, default=str))
        return 0

    if args.dry_run:
        print(f"LEVEL={level}")
        print(f"TITLE={title}")
        print(body)
        return 0

    from alerts import send_alert

    ok = send_alert(title, body, level=level, extra=extra, skip_debounce=True)
    print(f"[daily_digest] host={host} level={level} alert_sent={ok}", flush=True)
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
