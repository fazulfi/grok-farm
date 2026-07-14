#!/usr/bin/env python3
"""Daily fleet digest + proxy dashboard → Telegram (HTML card).

Default (fleet mode when backup.env present):
  1. Each host uploads a redacted snapshot JSON to S3 under
     farm-vps/fleet-digest/YYYY-MM-DD/<host>.json
  2. Only the **leader** host aggregates peers and sends **one** Telegram
     message for the whole fleet.

Per-host Telegram cards: `python daily_digest.py --local`
Proxy-only / dry-run / json still supported.

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
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

FARM = Path(os.path.expanduser(os.environ.get("GROK_FARM_DIR") or "~/grok-farm"))
DB = Path(os.path.expanduser(os.environ.get("GROK_AKUN_DB") or str(FARM / "akun.db")))
PROXY_FILE = FARM / "usa_proxies.txt"
BACKUP_ENV = Path(
    os.path.expanduser(
        os.environ.get("GROK_BACKUP_ENV") or "~/.config/grok-farm/backup.env"
    )
)

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


def _load_backup_env() -> bool:
    """Load S3/age backup.env into process env (no overrides of existing)."""
    if not BACKUP_ENV.is_file():
        return False
    try:
        for line in BACKUP_ENV.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k:
                os.environ.setdefault(k, v)
        return True
    except OSError:
        return False


def _py() -> str:
    venv = FARM / ".venv" / "bin" / "python"
    if venv.is_file():
        return str(venv)
    return sys.executable


def _env_truthy(name: str, default: str = "0") -> bool:
    return (os.environ.get(name) or default).strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _env_int(name: str, default: int) -> int:
    try:
        return int((os.environ.get(name) or str(default)).strip())
    except ValueError:
        return default


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
    """Return (title, plain_body, extra) for send_alert (single-host card)."""
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


def build_local_snapshot(
    host: str,
    health: Optional[dict[str, Any]],
    health_rc: int,
    proxy: dict[str, Any],
    domains: list[dict[str, Any]],
    *,
    proxy_only: bool,
) -> dict[str, Any]:
    """Compact redacted snapshot for S3 fleet aggregation."""
    acc = (health or {}).get("accounts") or {}
    th = (health or {}).get("token_health") or {}
    disk = (health or {}).get("disk") or {}
    backup = (health or {}).get("backup") or {}
    hard = (health or {}).get("issues_hard") or []
    soft = (health or {}).get("issues_soft") or []
    sp = (health or {}).get("soft_policy") or {}
    jo = sp.get("jwt_offline") or {}
    lp = sp.get("live_probe") or {}
    return {
        "schema": 1,
        "host": host,
        "ts_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "proxy_only": proxy_only,
        "health_rc": health_rc,
        "farmer": (health or {}).get("farmer"),
        "accounts": {
            "total": acc.get("total"),
            "injected": acc.get("injected"),
            "farmed": acc.get("farmed"),
            "error": acc.get("error"),
        },
        "tokens": {
            "ok": th.get("ok"),
            "expiring_soon": th.get("expiring_soon"),
            "expired": th.get("expired"),
            "invalid": th.get("invalid"),
        },
        "jwt_ok_pct": jo.get("ok_pct"),
        "jwt_expired_pct": jo.get("expired_pct"),
        "probe_needs_relogin": lp.get("needs_relogin"),
        "probe_alive": lp.get("alive"),
        "disk_free_gb": disk.get("free_gb"),
        "disk_used_pct": disk.get("used_pct"),
        "backup_status": backup.get("status"),
        "backup_age_h": backup.get("log_age_hours"),
        "issues_hard": list(hard),
        "issues_soft": list(soft),
        "health_error": (health or {}).get("error"),
        "proxy": {
            "file": proxy.get("proxy_file_count"),
            "tracked": proxy.get("tracked"),
            "soft_skip": proxy.get("soft_skip"),
            "disabled": proxy.get("disabled"),
            "threshold_consecutive": proxy.get("threshold_consecutive"),
            "fail_reasons": proxy.get("fail_reasons") or {},
            "top": (proxy.get("top") or [])[:4],
            "worst": (proxy.get("worst") or [])[:4],
            "error": proxy.get("error"),
        },
        "domains": domains[:6],
    }


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


def pick_fleet_level(snaps: list[dict[str, Any]]) -> str:
    worst = "success"
    rank = {"success": 0, "info": 1, "warning": 2, "error": 3, "critical": 4}
    for s in snaps:
        rc = int(s.get("health_rc") or 0)
        hard = s.get("issues_hard") or []
        pfile = int((s.get("proxy") or {}).get("file") or 0)
        if hard or rc == 3:
            lvl = "warning"
        elif rc not in (0, 2) and s.get("health_error"):
            lvl = "error"
        elif pfile <= 0:
            lvl = "warning"
        elif s.get("issues_soft") or rc == 2:
            lvl = "info"
        else:
            lvl = "success"
        if rank.get(lvl, 0) > rank.get(worst, 0):
            worst = lvl
    return worst


def _host_status_dot(s: dict[str, Any]) -> str:
    """🟢 healthy · 🟡 soft noise · 🔴 hard / farmer down · ⚪ unknown."""
    farmer = str(s.get("farmer") or "").lower()
    hard = s.get("issues_hard") or []
    soft = s.get("issues_soft") or []
    rc = int(s.get("health_rc") or 0)
    if hard or rc == 3 or farmer not in ("active", "activating", ""):
        if farmer and farmer not in ("active", "activating"):
            return "🔴"
        if hard or rc == 3:
            return "🔴"
    if soft or rc == 2:
        return "🟡"
    if farmer in ("active", "activating") or rc == 0:
        return "🟢"
    return "⚪"


def _kind_icon(kind: str) -> str:
    k = (kind or "").lower()
    if "inject" in k:
        return "💉"
    if "farm" in k:
        return "🌾"
    if "smoke" in k:
        return "🧪"
    if "health" in k or "digest" in k:
        return "🩺"
    return "⚡"


def _rel_age(ts: str) -> str:
    """Best-effort short age label from ISO-ish ts."""
    if not ts:
        return ""
    try:
        raw = ts.strip().replace("Z", "+00:00")
        if "T" not in raw and " " in raw:
            raw = raw.replace(" ", "T", 1)
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        sec = max(0, int((datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds()))
        if sec < 90:
            return "now"
        if sec < 3600:
            return f"{sec // 60}m"
        if sec < 86400:
            return f"{sec // 3600}h"
        return f"{sec // 86400}d"
    except (TypeError, ValueError):
        return ""


def format_fleet_body(
    snaps: list[dict[str, Any]],
    *,
    date_key: str,
    expected: list[str],
    missing: list[str],
    leader: str,
    title_override: Optional[str] = None,
) -> tuple[str, str, dict[str, Any]]:
    """True ops **dashboard** card (not a log dump) for sticky Telegram UI.

    Layout (top → bottom):
      1) KPI strip — hosts / injected / farmed / errors
      2) LIVE — last batch events (compact, no key=value soup)
      3) HOSTS — status lights + inj + JWT + proxy count
      4) PROXY — fleet pool health + top fail classes + worst sample
    Soft inventory noise (needs_relogin) is collapsed, not a wall of text.
    """
    title = title_override or f"Ops dashboard · {date_key}"
    ts = datetime.now(timezone.utc).strftime("%H:%M UTC")
    present = sorted({str(s.get("host") or "?") for s in snaps})
    n_exp = len(expected) if expected else len(present)
    n_ok_hosts = sum(1 for s in snaps if _host_status_dot(s) == "🟢")
    n_warn = sum(1 for s in snaps if _host_status_dot(s) == "🟡")
    n_bad = sum(1 for s in snaps if _host_status_dot(s) == "🔴")

    total_inj = 0
    total_farmed = 0
    total_err = 0
    total_soft_skip = 0
    total_disabled = 0
    total_tracked = 0
    total_proxy_file = 0
    reason_counts: Counter[str] = Counter()
    batch_ok = 0
    batch_fail = 0

    for s in snaps:
        acc = s.get("accounts") or {}
        total_inj += int(acc.get("injected") or 0)
        total_farmed += int(acc.get("farmed") or 0)
        total_err += int(acc.get("error") or 0)
        px = s.get("proxy") or {}
        total_soft_skip += int(px.get("soft_skip") or 0)
        total_disabled += int(px.get("disabled") or 0)
        total_tracked += int(px.get("tracked") or 0)
        total_proxy_file += int(px.get("file") or 0)
        for k, v in (px.get("fail_reasons") or {}).items():
            try:
                reason_counts[str(k)] += int(v)
            except (TypeError, ValueError):
                reason_counts[str(k)] += 1

    # --- KPI strip (dashboard header) ---
    host_chip = f"{len(present)}/{n_exp}"
    if missing:
        host_chip += f" · ⚠ missing {','.join(missing)}"
    lines: list[str] = [
        "FLEET:",
        f"  • 🖥️ Hosts  {host_chip}   🟢{n_ok_hosts}  🟡{n_warn}  🔴{n_bad}",
        f"  • 💉 Injected  {total_inj}     🌾 Farmed  {total_farmed}     ❌ Error  {total_err}",
        f"  • 🌐 Proxy pool  ~{total_proxy_file // max(1, len(snaps))}/host  · tracked {total_tracked}  · skip {total_soft_skip}  · off {total_disabled}",
        f"  • ⏱ {ts}  ·  refresh {leader}",
    ]

    # --- LIVE events (newest first) ---
    events: list[tuple[str, dict[str, Any], str]] = []
    for s in snaps:
        h = str(s.get("host") or "?")
        le = s.get("last_event")
        if isinstance(le, dict) and le.get("kind"):
            events.append((str(le.get("ts") or s.get("ts_utc") or ""), le, h))
    events.sort(key=lambda x: x[0], reverse=True)
    if events:
        lines.append("LIVE:")
        for ets, le, h in events[:6]:
            kind = str(le.get("kind") or "event")
            ok_n = int(le.get("ok") or 0)
            fail_n = int(le.get("fail") or 0)
            batch_ok += ok_n
            batch_fail += fail_n
            icon = _kind_icon(kind)
            age = _rel_age(ets)
            age_s = f" · {age}" if age else ""
            # status for this event
            if fail_n and not ok_n:
                mark = "🔴"
            elif fail_n and ok_n:
                mark = "🟡"
            else:
                mark = "🟢"
            score = f"+{ok_n}" if ok_n else "0"
            if fail_n:
                score += f" / −{fail_n}"
            label = kind.replace("_", " ")
            lines.append(f"  • {mark} {icon} <b>{h}</b>  {label}  {score}{age_s}")
            # emails as nested clean list (max 3) — no JWT
            emails = le.get("ok_emails") or le.get("emails") or []
            fail_emails = le.get("fail_emails") or []
            if isinstance(emails, list) and emails:
                for em in emails[:3]:
                    lines.append(f"      ✓ {em}")
                if len(emails) > 3:
                    lines.append(f"      … +{len(emails) - 3} more")
            if isinstance(fail_emails, list) and fail_emails:
                for em in fail_emails[:2]:
                    lines.append(f"      ✗ {em}")
            if le.get("fail_class") and fail_n:
                lines.append(f"      class: {le.get('fail_class')}")

    # --- HOST board ---
    lines.append("HOSTS:")
    for s in sorted(snaps, key=lambda x: str(x.get("host") or "")):
        h = s.get("host") or "?"
        acc = s.get("accounts") or {}
        inj = int(acc.get("injected") or 0)
        farmed = int(acc.get("farmed") or 0)
        err = int(acc.get("error") or 0)
        px = s.get("proxy") or {}
        hard = s.get("issues_hard") or []
        soft = s.get("issues_soft") or []
        # collapse soft inventory noise — show count only, not full list
        soft_noise = [x for x in soft if str(x) in ("needs_relogin", "many_expired_tokens")]
        soft_ops = [x for x in soft if x not in soft_noise]
        jwt = s.get("jwt_ok_pct")
        jwt_s = f"{jwt}%" if jwt is not None and jwt != "" else "—"
        farmer = str(s.get("farmer") or "?")
        farmer_short = "on" if farmer == "active" else farmer
        dot = _host_status_dot(s)
        # one clean row
        row = (
            f"  • {dot} <b>{h}</b>  "
            f"inj {inj}  farmed {farmed}  err {err}  "
            f"JWT {jwt_s}  proxy {px.get('file') or 0}  · {farmer_short}"
        )
        lines.append(row)
        if hard:
            lines.append(f"      🔴 hard: {', '.join(str(x) for x in hard[:4])}")
        if soft_ops:
            lines.append(f"      🟡 {', '.join(str(x) for x in soft_ops[:4])}")
        elif soft_noise and not hard:
            # one quiet chip instead of dumping needs_relogin walls
            lines.append(f"      · soft inventory ({len(soft_noise)})")

    # --- PROXY board ---
    lines.append("PROXY:")
    if reason_counts:
        top_r = " · ".join(f"{k} {v}" for k, v in reason_counts.most_common(5))
        lines.append(f"  • fail classes: {top_r}")
    else:
        lines.append("  • fail classes: (none yet)")
    worst_rows: list[str] = []
    for s in snaps:
        h = s.get("host") or "?"
        for p in (s.get("proxy") or {}).get("worst") or []:
            if int(p.get("fail") or 0) <= 0 and not p.get("disabled") and not p.get("soft_skip"):
                continue
            flag = "⛔" if p.get("disabled") else ("⏸" if p.get("soft_skip") else "·")
            reason = p.get("last_fail_reason") or ""
            rpart = f" · {reason}" if reason else ""
            worst_rows.append(
                f"  • {flag} [{h}] score {p.get('score')}  "
                f"{p.get('ok')}/{p.get('fail')}  {p.get('proxy')}{rpart}"
            )
    if worst_rows:
        lines.append(f"  • worst ({min(4, len(worst_rows))}):")
        lines.extend(worst_rows[:4])

    # Footer one line only — not a log wall
    lines.append("1 sticky card · inventory only")
    body = "\n".join(lines)
    if len(body) > 3400:
        body = body[:3350] + "\n…(truncated)"

    extra: dict[str, Any] = {
        "dashboard": True,  # clean UI renderer (no key=value meta dump)
        "ok": batch_ok if (batch_ok or batch_fail) else total_inj,
        "fail": batch_fail if (batch_ok or batch_fail) else (total_farmed + total_err),
        "injected": total_inj,
        "hosts": f"{len(present)}/{n_exp}",
        "hosts_present": present,
        "hosts_missing": missing,
    }
    return title, body, extra


def ops_dashboard_enabled() -> bool:
    """GROK_OPS_DASHBOARD default on — single sticky fleet card updated per batch."""
    raw = (os.environ.get("GROK_OPS_DASHBOARD") or "1").strip().lower()
    return raw not in ("0", "false", "off", "no")


def batch_alerts_enabled() -> bool:
    """Per-batch new Telegram messages. Default OFF when ops dashboard on."""
    raw = (os.environ.get("GROK_BATCH_ALERTS") or "").strip().lower()
    if raw in ("1", "true", "on", "yes"):
        return True
    if raw in ("0", "false", "off", "no"):
        return False
    # Default: suppress batch spam when dashboard is on
    return not ops_dashboard_enabled()


def sticky_name() -> str:
    return (os.environ.get("GROK_OPS_DASHBOARD_STICKY_NAME") or "fleet_digest").strip() or "fleet_digest"


def publish_ops_event(
    event: dict[str, Any],
    *,
    refresh_telegram: bool = True,
    wait_peers: bool = False,
) -> bool:
    """Upload host snapshot with last_event and refresh the single sticky dashboard.

    Called after each farm/inject batch so Telegram shows one live-updating card
    (editMessageText) instead of spamming new messages. Any host may edit when
    sticky message_id is shared on S3.

    event keys (all optional except kind): kind, ok, fail, level, batch_id,
    title, fail_class, ok_emails (list, no JWT), fail_emails, note.
    Never includes password/JWT. Returns True if sticky edit/send ok (or upload-only).
    """
    _load_env()
    if not ops_dashboard_enabled():
        return False
    if not fleet_s3_ready():
        print("[ops_dashboard] S3 not ready — skip sticky refresh", flush=True)
        return False

    host, health, health_rc, proxy, domains = collect_local(proxy_only=False)
    date_key = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    snapshot = build_local_snapshot(
        host, health, health_rc, proxy, domains, proxy_only=False
    )
    # Attach last_event (redacted emails only)
    kind = str(event.get("kind") or "event")
    ok_emails = event.get("ok_emails") or event.get("emails") or []
    if not isinstance(ok_emails, list):
        ok_emails = []
    fail_emails = event.get("fail_emails") or []
    if not isinstance(fail_emails, list):
        fail_emails = []
    # Cap email samples (S7 — no tokens)
    ok_emails = [str(e)[:120] for e in ok_emails[:12] if str(e).strip()]
    fail_emails = [str(e)[:120] for e in fail_emails[:12] if str(e).strip()]
    last_event: dict[str, Any] = {
        "kind": kind,
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ok": int(event.get("ok") or 0),
        "fail": int(event.get("fail") or 0),
        "level": str(event.get("level") or "info"),
        "batch_id": str(event.get("batch_id") or "")[:80],
        "title": str(event.get("title") or kind)[:120],
        "fail_class": str(event.get("fail_class") or "")[:80],
        "ok_emails": ok_emails,
        "fail_emails": fail_emails,
        "note": str(event.get("note") or "")[:200],
    }
    snapshot["last_event"] = last_event
    snapshot["schema"] = 2

    try:
        key = upload_fleet_snapshot(snapshot, date_key)
        print(f"[ops_dashboard] uploaded s3://…/{key} event={kind}", flush=True)
    except Exception as e:
        print(f"[ops_dashboard] upload failed: {e}", flush=True)
        return False

    if not refresh_telegram:
        return True

    # Aggregate peers without long wait (per-batch must be fast)
    wait_sec = 0
    if wait_peers:
        wait_sec = _env_int("GROK_FLEET_DIGEST_WAIT_SEC", 30)
    expected = expected_hosts()
    snaps_map: dict[str, dict[str, Any]] = {}
    deadline = time.time() + wait_sec
    while True:
        try:
            snaps_map = list_fleet_snapshots(date_key)
        except Exception as e:
            print(f"[ops_dashboard] list snapshots error: {e}", flush=True)
            snaps_map = {host: snapshot}
        snaps_map.setdefault(host, snapshot)
        present = set(snaps_map.keys())
        missing = [h for h in expected if h not in present]
        if not missing or time.time() >= deadline or wait_sec <= 0:
            break
        time.sleep(min(5, max(1, int(deadline - time.time()))))

    snaps = list(snaps_map.values())
    missing = [h for h in expected if h not in snaps_map]
    title, body, extra = format_fleet_body(
        snaps,
        date_key=date_key,
        expected=expected,
        missing=missing,
        leader=host,
        title_override=f"Ops dashboard · {date_key}",
    )
    level = pick_fleet_level(snaps)
    # Prefer event level if worse than fleet health level
    rank = {"success": 0, "info": 1, "warning": 2, "error": 3, "critical": 4}
    ev_level = str(last_event.get("level") or "info")
    if rank.get(ev_level, 0) > rank.get(level, 0):
        level = ev_level

    from alerts import send_or_edit_sticky

    sticky = (os.environ.get("GROK_FLEET_DIGEST_STICKY") or "1").strip().lower()
    force_new = sticky in ("0", "false", "off", "no")
    ok = send_or_edit_sticky(
        title,
        body,
        level=level,
        extra=extra,
        sticky_name=sticky_name(),
        force_new=force_new,
    )
    print(
        f"[ops_dashboard] sticky host={host} event={kind} level={level} "
        f"hosts={len(snaps)} alert_sent={ok}",
        flush=True,
    )
    return ok


def _s3_client():
    import boto3
    from botocore.client import Config

    return boto3.client(
        "s3",
        endpoint_url=os.environ["S3_ENDPOINT"],
        aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"],
        region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
        ),
    )


def fleet_s3_ready() -> bool:
    if not _load_backup_env():
        return False
    need = ("S3_ENDPOINT", "S3_BUCKET", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY")
    return all((os.environ.get(k) or "").strip() for k in need)


def fleet_prefix(date_key: str) -> str:
    base = (os.environ.get("GROK_FLEET_DIGEST_PREFIX") or "farm-vps/fleet-digest").strip().strip("/")
    return f"{base}/{date_key}"


def upload_fleet_snapshot(snapshot: dict[str, Any], date_key: str) -> str:
    """Upload snapshot JSON; returns s3 key. Never logs secrets."""
    host = str(snapshot.get("host") or _hostname())
    bucket = os.environ["S3_BUCKET"]
    key = f"{fleet_prefix(date_key)}/{host}.json"
    body = json.dumps(snapshot, separators=(",", ":"), default=str).encode("utf-8")
    client = _s3_client()
    client.put_object(Bucket=bucket, Key=key, Body=body, ContentLength=len(body))
    return key


def list_fleet_snapshots(date_key: str) -> dict[str, dict[str, Any]]:
    """Map host → snapshot for date."""
    import boto3
    from botocore.client import Config

    bucket = os.environ["S3_BUCKET"]
    pref = fleet_prefix(date_key) + "/"
    client = boto3.client(
        "s3",
        endpoint_url=os.environ["S3_ENDPOINT"],
        aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"],
        region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )
    out: dict[str, dict[str, Any]] = {}
    token = None
    while True:
        kwargs: dict[str, Any] = {"Bucket": bucket, "Prefix": pref}
        if token:
            kwargs["ContinuationToken"] = token
        resp = client.list_objects_v2(**kwargs)
        for obj in resp.get("Contents") or []:
            key = obj["Key"]
            base = key.rsplit("/", 1)[-1]
            if not base.endswith(".json"):
                continue
            host = base[:-5]
            try:
                raw = client.get_object(Bucket=bucket, Key=key)["Body"].read()
                data = json.loads(raw.decode("utf-8"))
                if isinstance(data, dict):
                    data.setdefault("host", host)
                    out[host] = data
            except Exception as e:
                out[host] = {"host": host, "health_error": f"read_fail:{e}", "health_rc": 1}
        if not resp.get("IsTruncated"):
            break
        token = resp.get("NextContinuationToken")
    return out


def expected_hosts() -> list[str]:
    raw = (os.environ.get("GROK_FLEET_DIGEST_HOSTS") or "").strip()
    if not raw:
        return ["grok3", "grok4", "grok5", "grok6"]
    return [h.strip() for h in raw.split(",") if h.strip()]


def is_fleet_leader(host: str) -> bool:
    if _env_truthy("GROK_FLEET_DIGEST_LEADER", "0"):
        return True
    leader_host = (os.environ.get("GROK_FLEET_DIGEST_LEADER_HOST") or "").strip()
    if leader_host:
        return host == leader_host
    # Default leader: first alphabetically among expected list (stable)
    exp = expected_hosts()
    if exp:
        return host == sorted(exp)[0]
    return True


def fleet_mode_enabled(*, force_fleet: bool, force_local: bool) -> bool:
    if force_local:
        return False
    if force_fleet:
        return fleet_s3_ready()
    if (os.environ.get("GROK_FLEET_DIGEST") or "1").strip().lower() in (
        "0",
        "false",
        "off",
        "no",
    ):
        return False
    return fleet_s3_ready()


def collect_local(
    *,
    proxy_only: bool,
) -> tuple[str, Optional[dict[str, Any]], int, dict[str, Any], list[dict[str, Any]]]:
    host = _hostname()
    health: Optional[dict[str, Any]] = None
    health_rc = 0
    if not proxy_only:
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
    return host, health, health_rc, proxy, domains


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Grok Farm daily digest + proxy dashboard (fleet or local)"
    )
    parser.add_argument("--proxy-only", action="store_true", help="Proxy dashboard only")
    parser.add_argument("--dry-run", action="store_true", help="Print only, no Telegram/S3")
    parser.add_argument("--json", action="store_true", help="Print JSON payload")
    parser.add_argument(
        "--local",
        action="store_true",
        help="Force per-host Telegram card (skip fleet aggregation)",
    )
    parser.add_argument(
        "--fleet",
        action="store_true",
        help="Force fleet mode (requires backup.env / S3)",
    )
    parser.add_argument(
        "--no-wait",
        action="store_true",
        help="Leader: do not wait for peer snapshots",
    )
    args = parser.parse_args()

    _load_env()
    host, health, health_rc, proxy, domains = collect_local(proxy_only=args.proxy_only)
    date_key = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    snapshot = build_local_snapshot(
        host, health, health_rc, proxy, domains, proxy_only=args.proxy_only
    )
    level_local = pick_level(health, health_rc, proxy)
    title_local, body_local, extra_local = format_digest_body(
        host, health, health_rc, proxy, domains, proxy_only=args.proxy_only
    )

    use_fleet = fleet_mode_enabled(force_fleet=args.fleet, force_local=args.local)

    if args.json:
        payload: dict[str, Any] = {
            "mode": "fleet" if use_fleet else "local",
            "host": host,
            "date": date_key,
            "leader": is_fleet_leader(host) if use_fleet else None,
            "snapshot": snapshot,
            "title": title_local,
            "body": body_local,
            "level": level_local,
            "extra": extra_local,
        }
        print(json.dumps(payload, indent=2, default=str))
        return 0

    if args.dry_run:
        print(f"MODE={'fleet' if use_fleet else 'local'} LEADER={is_fleet_leader(host)}")
        print(f"LEVEL={level_local}")
        print(f"TITLE={title_local}")
        print(body_local)
        if use_fleet:
            print(f"SNAPSHOT_HOST={host} DATE={date_key} (would upload to S3)")
        return 0

    # ── Fleet path: upload all; leader aggregates one Telegram ────────────
    if use_fleet:
        try:
            key = upload_fleet_snapshot(snapshot, date_key)
            print(f"[daily_digest] uploaded s3://…/{key}", flush=True)
        except Exception as e:
            print(f"[daily_digest] fleet upload failed: {e}; fallback local", flush=True)
            use_fleet = False

    if use_fleet and not is_fleet_leader(host):
        print(
            f"[daily_digest] host={host} follower — snapshot uploaded, no Telegram",
            flush=True,
        )
        return 0

    if use_fleet and is_fleet_leader(host):
        wait_sec = 0 if args.no_wait else _env_int("GROK_FLEET_DIGEST_WAIT_SEC", 900)
        poll = max(5, _env_int("GROK_FLEET_DIGEST_POLL_SEC", 30))
        expected = expected_hosts()
        deadline = time.time() + wait_sec
        snaps_map: dict[str, dict[str, Any]] = {}
        while True:
            try:
                snaps_map = list_fleet_snapshots(date_key)
            except Exception as e:
                print(f"[daily_digest] list snapshots error: {e}", flush=True)
                snaps_map = {host: snapshot}
            # Always include our own snapshot
            snaps_map.setdefault(host, snapshot)
            present = set(snaps_map.keys())
            missing = [h for h in expected if h not in present]
            if not missing or time.time() >= deadline or wait_sec <= 0:
                break
            print(
                f"[daily_digest] leader waiting peers missing={missing} "
                f"have={sorted(present)}",
                flush=True,
            )
            time.sleep(min(poll, max(1, int(deadline - time.time()))))

        snaps = list(snaps_map.values())
        missing = [h for h in expected if h not in snaps_map]
        title, body, extra = format_fleet_body(
            snaps,
            date_key=date_key,
            expected=expected,
            missing=missing,
            leader=host,
        )
        level = pick_fleet_level(snaps)
        # Sticky live card: editMessageText same message_id (1 fleet card, updates in place)
        # Use ops dashboard title when GROK_OPS_DASHBOARD=1 (single always-on card)
        from alerts import send_or_edit_sticky

        sticky = (os.environ.get("GROK_FLEET_DIGEST_STICKY") or "1").strip().lower()
        force_new = sticky in ("0", "false", "off", "no")
        if ops_dashboard_enabled():
            title = f"Ops dashboard · {date_key}"
        ok = send_or_edit_sticky(
            title,
            body,
            level=level,
            extra=extra,
            sticky_name=sticky_name(),
            force_new=force_new,
        )
        print(
            f"[daily_digest] FLEET host={host} level={level} "
            f"hosts={len(snaps)} missing={missing} sticky={not force_new} alert_sent={ok}",
            flush=True,
        )
        return 0 if ok else 2

    # ── Local single-host card ────────────────────────────────────────────
    # When ops dashboard is on, still try sticky (local mid only if no S3)
    if ops_dashboard_enabled():
        from alerts import send_or_edit_sticky

        ok = send_or_edit_sticky(
            title_local,
            body_local,
            level=level_local,
            extra=extra_local,
            sticky_name=sticky_name(),
        )
        print(
            f"[daily_digest] LOCAL sticky host={host} level={level_local} alert_sent={ok}",
            flush=True,
        )
        return 0 if ok else 2

    from alerts import send_alert

    ok = send_alert(
        title_local, body_local, level=level_local, extra=extra_local, skip_debounce=True
    )
    print(
        f"[daily_digest] LOCAL host={host} level={level_local} alert_sent={ok}",
        flush=True,
    )
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
