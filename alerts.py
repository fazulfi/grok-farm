#!/usr/bin/env python3
"""Optional webhook alerts (Discord/Telegram-compatible generic JSON)."""
from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional

from log_redact import redact

DEFAULT_DEBOUNCE_MIN = 60


def webhook_url() -> Optional[str]:
    return os.environ.get("GROK_ALERT_WEBHOOK") or os.environ.get("GROK_FARM_ALERT_WEBHOOK")


def _debounce_minutes() -> int:
    raw = (os.environ.get("GROK_ALERT_DEBOUNCE_MIN") or "").strip()
    try:
        n = int(raw) if raw else DEFAULT_DEBOUNCE_MIN
    except ValueError:
        n = DEFAULT_DEBOUNCE_MIN
    return max(0, n)


def _debounce_dir() -> Path:
    farm = Path(os.path.expanduser(os.environ.get("GROK_FARM_DIR") or "~/grok-farm"))
    d = farm / "logs" / "alert_debounce"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        d = Path(os.environ.get("TMPDIR") or os.environ.get("TEMP") or "/tmp") / "grok-farm-alert-debounce"
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
    return d


def _debounce_key(title: str, body: str, level: str, extra: Optional[dict[str, Any]]) -> str:
    issues = ""
    if extra and isinstance(extra.get("issues"), (list, tuple)):
        issues = ",".join(str(x) for x in extra["issues"])
    elif extra and extra.get("issues"):
        issues = str(extra["issues"])
    raw = f"{level}|{title}|{issues}|{body[:200]}"
    return hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:32]


def _should_send(title: str, body: str, level: str, extra: Optional[dict[str, Any]]) -> bool:
    """Return False if same alert fingerprint was sent within debounce window."""
    mins = _debounce_minutes()
    if mins <= 0:
        return True
    key = _debounce_key(title, body, level, extra)
    path = _debounce_dir() / f"{key}.ts"
    now = time.time()
    try:
        if path.is_file():
            prev = float(path.read_text(encoding="utf-8").strip() or "0")
            if now - prev < mins * 60:
                return False
        path.write_text(str(now), encoding="utf-8")
    except OSError:
        # fail-open: send alert if debounce file IO fails
        return True
    return True


def send_alert(title: str, body: str = "", level: str = "info", extra: Optional[dict[str, Any]] = None) -> bool:
    """
    Fire alert if GROK_ALERT_WEBHOOK is set.
    Discord: expects {"content": "..."}.
    Generic: posts JSON {title, body, level, ...}.
    Never includes raw JWTs — body is redacted.
    Debounce: GROK_ALERT_DEBOUNCE_MIN (default 60) minutes per title+issues fingerprint.
    """
    url = webhook_url()
    if not url:
        return False
    if not _should_send(title, body, level, extra):
        return False
    safe_title = redact(title)[:200]
    safe_body = redact(body)[:1800]
    content = f"**[{level.upper()}] Grok Farm — {safe_title}**\n{safe_body}".strip()
    payload: dict[str, Any] = {
        "content": content,
        "title": safe_title,
        "body": safe_body,
        "level": level,
    }
    if extra:
        # shallow redact string values
        payload["extra"] = {k: redact(v) if isinstance(v, str) else v for k, v in extra.items()}
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "User-Agent": "grok-farm-alerts/1.0"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return 200 <= getattr(resp, "status", 200) < 300
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        print(f"[ALERT] webhook failed: {redact(e)}")
        return False
