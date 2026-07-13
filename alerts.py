#!/usr/bin/env python3
"""Optional webhook alerts (Discord/Telegram-compatible generic JSON)."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Optional

from log_redact import redact


def webhook_url() -> Optional[str]:
    return os.environ.get("GROK_ALERT_WEBHOOK") or os.environ.get("GROK_FARM_ALERT_WEBHOOK")


def send_alert(title: str, body: str = "", level: str = "info", extra: Optional[dict[str, Any]] = None) -> bool:
    """
    Fire alert if GROK_ALERT_WEBHOOK is set.
    Discord: expects {"content": "..."}.
    Generic: posts JSON {title, body, level, ...}.
    Never includes raw JWTs — body is redacted.
    """
    url = webhook_url()
    if not url:
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
