#!/usr/bin/env python3
"""Recommend farm concurrent from domain_stats + proxy_stats health scores.

Used by brutal_farmer.sh each batch (live-safe: next loop picks up new value).

Env:
  GROK_CONCURRENT            base concurrent (default 3)
  GROK_CONCURRENT_MIN        floor (default 1)
  GROK_CONCURRENT_MAX        ceiling (default 5; do not exceed without env)
  GROK_ADAPTIVE_CONCURRENT   1|0 enable adaptive (default 1)
  GROK_AKUN_DB               path to akun.db

CLI:
  --print   print single integer concurrent
  --json    full breakdown JSON
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any, Optional

from db_schema import connect, domain_max_consecutive_fails, migrate

DEFAULT_BASE = 3
DEFAULT_MIN = 1
DEFAULT_MAX = 5
PROXY_TOP_N = 20


def _env_int(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_bool01(name: str, default: bool = True) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    if raw in ("0", "false", "no", "off"):
        return False
    if raw in ("1", "true", "yes", "on"):
        return True
    return default


def _load_dotenv() -> None:
    """Best-effort load farm .env so CLI matches systemd/farmer env."""
    farm = Path(os.path.expanduser(os.environ.get("GROK_FARM_DIR") or "~/grok-farm"))
    env_path = farm / ".env"
    if not env_path.is_file():
        # script lives next to .env on VPS checkout
        here = Path(__file__).resolve().parent / ".env"
        if here.is_file():
            env_path = here
        else:
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


def _avg_domain_score(conn: sqlite3.Connection) -> tuple[float, int, int, int]:
    """Avg score of non-disabled domains; max consecutive_fails; count; high-fail flag.

    Returns (avg_score, n_domains, max_consecutive_fails, high_fail_anywhere).
    Empty table → score 0.5.
    """
    thr = domain_max_consecutive_fails()
    try:
        rows = conn.execute(
            """SELECT score, consecutive_fails, disabled
               FROM domain_stats"""
        ).fetchall()
    except sqlite3.Error:
        return 0.5, 0, 0, 0

    scores: list[float] = []
    max_cf = 0
    high_anywhere = 0
    for r in rows:
        try:
            disabled = int(r["disabled"] or 0)
            score = float(r["score"] if r["score"] is not None else 0.5)
            cf = int(r["consecutive_fails"] or 0)
        except (TypeError, KeyError, IndexError, ValueError):
            continue
        if cf > max_cf:
            max_cf = cf
        # "high consecutive fails anywhere" (disabled domains still count)
        if cf >= thr:
            high_anywhere = 1
        if disabled:
            continue
        scores.append(max(0.0, min(1.0, score)))

    if not scores:
        return 0.5, 0, max_cf, high_anywhere
    avg = sum(scores) / len(scores)
    return avg, len(scores), max_cf, high_anywhere


def _avg_proxy_score(conn: sqlite3.Connection, top_n: int = PROXY_TOP_N) -> tuple[float, int]:
    """Average score of top-N proxies by score (or all if fewer). Empty → 0.5."""
    try:
        rows = conn.execute(
            """SELECT score FROM proxy_stats
               ORDER BY score DESC
               LIMIT ?""",
            (max(1, top_n),),
        ).fetchall()
    except sqlite3.Error:
        return 0.5, 0

    scores: list[float] = []
    for r in rows:
        try:
            score = float(r["score"] if r["score"] is not None else 0.5)
        except (TypeError, KeyError, IndexError, ValueError):
            continue
        scores.append(max(0.0, min(1.0, score)))

    if not scores:
        return 0.5, 0
    return sum(scores) / len(scores), len(scores)


def _clamp(n: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, n))


def recommend_concurrent(conn: Optional[sqlite3.Connection] = None) -> dict[str, Any]:
    """Return concurrent recommendation dict.

    Keys: concurrent, base, min, max, domain_score, proxy_score, reason
    Plus diagnostics: adaptive, domain_n, proxy_n, max_consecutive_fails, factor
    """
    base = max(1, _env_int("GROK_CONCURRENT", DEFAULT_BASE))
    min_c = max(1, _env_int("GROK_CONCURRENT_MIN", DEFAULT_MIN))
    max_c = max(min_c, _env_int("GROK_CONCURRENT_MAX", DEFAULT_MAX))
    adaptive = _env_bool01("GROK_ADAPTIVE_CONCURRENT", True)

    out: dict[str, Any] = {
        "concurrent": _clamp(base, min_c, max_c),
        "base": base,
        "min": min_c,
        "max": max_c,
        "domain_score": 0.5,
        "proxy_score": 0.5,
        "reason": "adaptive_off",
        "adaptive": adaptive,
        "domain_n": 0,
        "proxy_n": 0,
        "max_consecutive_fails": 0,
        "factor": 1.0,
    }

    if not adaptive:
        out["reason"] = "adaptive_disabled"
        return out

    own_conn = conn is None
    if own_conn:
        try:
            conn = connect()
            migrate(conn)
        except Exception as e:
            out["reason"] = f"db_unavailable:{e}"
            return out

    assert conn is not None
    try:
        domain_score, domain_n, max_cf, high_fail = _avg_domain_score(conn)
        proxy_score, proxy_n = _avg_proxy_score(conn)
    except Exception as e:
        out["reason"] = f"score_error:{e}"
        return out
    finally:
        if own_conn:
            try:
                conn.close()
            except Exception:
                pass

    out["domain_score"] = round(domain_score, 4)
    out["proxy_score"] = round(proxy_score, 4)
    out["domain_n"] = domain_n
    out["proxy_n"] = proxy_n
    out["max_consecutive_fails"] = max_cf

    # Map combined health → factor around 1.0 (mid score → ~base)
    combined = 0.55 * domain_score + 0.45 * proxy_score
    f = 0.5 + combined  # 0.5 .. 1.5

    thr = domain_max_consecutive_fails()
    # Bias down: weak domains or high consecutive fails anywhere (incl. disabled)
    high_consec = bool(high_fail) or max_cf >= thr
    # Bias up requires no domain with consec >= 3 (task fixed threshold)
    any_consec_ge3 = max_cf >= 3

    if domain_score < 0.4 or high_consec:
        f *= 0.65
        reason = (
            f"bias_down domain_score={domain_score:.3f} "
            f"max_cf={max_cf} high_fail={bool(high_fail)}"
        )
    elif domain_score >= 0.75 and proxy_score >= 0.75 and not any_consec_ge3:
        f *= 1.25
        reason = (
            f"bias_up domain_score={domain_score:.3f} "
            f"proxy_score={proxy_score:.3f}"
        )
    else:
        reason = (
            f"neutral domain_score={domain_score:.3f} "
            f"proxy_score={proxy_score:.3f} combined={combined:.3f}"
        )

    recommended = _clamp(int(round(base * f)), min_c, max_c)
    out["factor"] = round(f, 4)
    out["concurrent"] = recommended
    out["reason"] = reason
    return out


def main(argv: Optional[list[str]] = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    _load_dotenv()
    want_print = "--print" in args
    want_json = "--json" in args

    rec = recommend_concurrent()

    if want_json:
        print(json.dumps(rec, indent=2, sort_keys=True))
    elif want_print:
        print(int(rec["concurrent"]))
    else:
        # human one-liner
        print(
            f"concurrent={rec['concurrent']} "
            f"(base={rec['base']} min={rec['min']} max={rec['max']} "
            f"domain={rec['domain_score']} proxy={rec['proxy_score']}) "
            f"{rec['reason']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
