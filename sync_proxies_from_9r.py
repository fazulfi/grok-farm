#!/usr/bin/env python3
"""Sync 9router proxyPools -> usa_proxies.txt for farm.py (host:port:user:pass).

Uses gateway list_proxies.py which soft-filters isActive/testStatus when present.
Fail-open if remote returns empty after filter (already handled gateway-side).
Never deletes proxyPools rows.
"""
import os
import subprocess
from urllib.parse import urlparse

OUT = os.path.expanduser("~/grok-farm/usa_proxies.txt")
SSH = [
    "ssh",
    "-i",
    os.path.expanduser("~/.ssh/id_ed25519"),
    "-o",
    "StrictHostKeyChecking=no",
    "-o",
    "ConnectTimeout=15",
    "root@GW_IP",
    "-p",
    "39999",
]


def fetch_urls():
    r = subprocess.run(
        SSH + ["python3", "/root/list_proxies.py"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    urls = []
    for line in (r.stdout or "").splitlines():
        line = line.strip()
        if line.startswith("#"):
            # soft-filter warn from list_proxies
            print(f"[SYNC] {line}")
            continue
        if line.startswith("COUNT="):
            continue
        if line.startswith("http"):
            urls.append(line.rstrip("/"))
    return urls


def url_to_line(url: str):
    p = urlparse(url)
    if not p.hostname or not p.port:
        return None
    user = p.username or ""
    pw = p.password or ""
    if user and pw:
        return f"{p.hostname}:{p.port}:{user}:{pw}"
    return f"{p.hostname}:{p.port}"


def main():
    urls = fetch_urls()
    lines = []
    seen = set()
    for u in urls:
        line = url_to_line(u)
        if line and line not in seen:
            seen.add(line)
            lines.append(line)
    if not lines:
        print("[SYNC] ERROR: no proxies from 9router proxyPools")
        return 1
    with open(OUT, "w") as f:
        f.write("\n".join(lines) + "\n")
    print(f"[SYNC] Wrote {len(lines)} proxies from 9router proxyPools -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
