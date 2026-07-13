#!/usr/bin/env python3
"""Optional webhook / Telegram alerts (Discord-compatible + Bot API)."""
from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Optional

from log_redact import redact

DEFAULT_DEBOUNCE_MIN = 60


def webhook_url() -> Optional[str]:
    return os.environ.get("GROK_ALERT_WEBHOOK") or os.environ.get("GROK_FARM_ALERT_WEBHOOK")


def telegram_bot_token() -> Optional[str]:
    t = (os.environ.get("GROK_TELEGRAM_BOT_TOKEN") or "").strip()
    return t or None


def telegram_chat_id() -> Optional[str]:
    c = (os.environ.get("GROK_TELEGRAM_CHAT_ID") or "").strip()
    return c or None


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


def _post_json(url: str, payload: dict[str, Any], timeout: float = 15) -> bool:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "User-Agent": "grok-farm-alerts/1.1"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 200 <= getattr(resp, "status", 200) < 300
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        print(f"[ALERT] post failed: {redact(e)}")
        return False


def _send_telegram(text: str) -> bool:
    """Send via Telegram Bot API when token + chat_id are set. Never logs token."""
    token = telegram_bot_token()
    chat_id = telegram_chat_id()
    if not token or not chat_id:
        return False
    # Bot API: POST /bot<token>/sendMessage
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text[:4000],
        "disable_web_page_preview": True,
    }
    ok = _post_json(url, payload)
    if not ok:
        print("[ALERT] telegram sendMessage failed (token/chat_id redacted)")
    return ok


def _send_webhook(url: str, content: str, safe_title: str, safe_body: str, level: str, extra: Optional[dict[str, Any]]) -> bool:
    # Telegram Bot API URL form: https://api.telegram.org/botTOKEN/sendMessage
    if "api.telegram.org" in url and "sendMessage" in url:
        payload: dict[str, Any] = {
            "chat_id": telegram_chat_id() or "",
            "text": content[:4000],
            "disable_web_page_preview": True,
        }
        # If chat_id embedded as query ?chat_id=
        try:
            parsed = urllib.parse.urlparse(url)
            qs = urllib.parse.parse_qs(parsed.query)
            if qs.get("chat_id"):
                payload["chat_id"] = qs["chat_id"][0]
        except Exception:
            pass
        if not payload.get("chat_id"):
            print("[ALERT] telegram webhook URL missing chat_id")
            return False
        return _post_json(url.split("?")[0], payload)

    payload = {
        "content": content,
        "title": safe_title,
        "body": safe_body,
        "level": level,
    }
    if extra:
        payload["extra"] = {k: redact(v) if isinstance(v, str) else v for k, v in extra.items()}
    return _post_json(url, payload)


def send_alert(title: str, body: str = "", level: str = "info", extra: Optional[dict[str, Any]] = None) -> bool:
    """
    Fire alert if Telegram (token+chat_id) and/or GROK_ALERT_WEBHOOK is set.
    Discord: expects {"content": "..."}.
    Telegram: Bot API sendMessage (GROK_TELEGRAM_BOT_TOKEN + GROK_TELEGRAM_CHAT_ID).
    Never includes raw JWTs — body is redacted. Never logs bot token.
    Debounce: GROK_ALERT_DEBOUNCE_MIN (default 60) minutes per title+issues fingerprint.
    """
    url = webhook_url()
    has_tg = bool(telegram_bot_token() and telegram_chat_id())
    if not url and not has_tg:
        return False
    if not _should_send(title, body, level, extra):
        return False
    safe_title = redact(title)[:200]
    safe_body = redact(body)[:1800]
    content = f"**[{level.upper()}] Grok Farm — {safe_title}**\n{safe_body}".strip()
    # Prefer Telegram plain text (no markdown required)
    tg_text = f"[{level.upper()}] Grok Farm — {safe_title}\n{safe_body}".strip()

    sent = False
    if has_tg:
        sent = _send_telegram(tg_text) or sent
    if url:
        sent = _send_webhook(url, content, safe_title, safe_body, level, extra) or sent
    return sent
