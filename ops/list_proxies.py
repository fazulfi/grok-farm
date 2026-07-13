#!/usr/bin/env python3
"""List proxyUrls from 9router proxyPools (gateway-side helper).

Soft-filters inactive / bad testStatus when those fields exist.
Fail-open: if filter would empty the pool, print all URLs with proxyUrl.
Never deletes proxyPools rows.
"""
import json
import sqlite3

DB = "/var/lib/9router/db/data.sqlite"

# Known-bad testStatus values (case-insensitive). Missing field = usable.
_BAD_TEST = frozenset(
    {
        "fail",
        "failed",
        "error",
        "dead",
        "inactive",
        "bad",
        "offline",
        "down",
    }
)


def _is_active(obj: dict) -> bool:
    """True if proxy should be listed. Missing isActive/testStatus = keep (fail-open fields)."""
    if "isActive" in obj:
        v = obj.get("isActive")
        if v is False or v == 0 or v == "0":
            return False
        if isinstance(v, str) and v.strip().lower() in ("false", "no", "off", "inactive"):
            return False
    # alternate boolean keys seen in some gateway dumps
    for key in ("active", "enabled"):
        if key in obj:
            v = obj.get(key)
            if v is False or v == 0 or v == "0":
                return False
    ts = obj.get("testStatus") or obj.get("status") or obj.get("lastTestStatus") or ""
    if isinstance(ts, str) and ts.strip().lower() in _BAD_TEST:
        return False
    return True


def main() -> int:
    c = sqlite3.connect(DB)
    rows = c.execute("SELECT data FROM proxyPools").fetchall()
    c.close()

    all_urls: list[str] = []
    active_urls: list[str] = []
    for (d,) in rows:
        try:
            o = json.loads(d)
            u = (o.get("proxyUrl") or "").strip().rstrip("/")
            if not u:
                continue
            all_urls.append(u)
            if _is_active(o if isinstance(o, dict) else {}):
                active_urls.append(u)
        except Exception as e:
            print(f"ERR {e}")

    # fail-open: never return empty when pools have URLs
    out = active_urls if active_urls else all_urls
    print(f"COUNT={len(out)}")
    if all_urls and not active_urls:
        print(f"# WARN soft-filter emptied pool; fail-open using all {len(all_urls)}")
    for u in out:
        print(u)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
