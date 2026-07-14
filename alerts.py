#!/usr/bin/env python3
"""Optional webhook / Telegram alerts (Discord-compatible + Bot API HTML cards)."""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Optional

from log_redact import redact

DEFAULT_DEBOUNCE_MIN = 60

# Level → (emoji, short label) for rich Telegram cards
LEVEL_META: dict[str, tuple[str, str]] = {
    "info": ("🟢", "INFO"),
    "success": ("✅", "OK"),
    "warning": ("🟡", "WARN"),
    "critical": ("🔴", "CRIT"),
    "error": ("🔴", "ERROR"),
    "debug": ("⚪", "DEBUG"),
}

# Telegram HTML: escape user-controlled text before wrapping tags
_HTML_SPECIAL = (("&", "&amp;"), ("<", "&lt;"), (">", "&gt;"))


def html_escape(text: str) -> str:
    """Escape &, <, > for Telegram parse_mode=HTML (order matters: & first)."""
    s = str(text)
    for a, b in _HTML_SPECIAL:
        s = s.replace(a, b)
    return s


# Always load farm .env so workflow/health (no farm.py dotenv) still see Telegram keys.
# Never logs secret values.
def _load_farm_env() -> None:
    root = Path(__file__).resolve().parent
    env_path = root / ".env"
    if not env_path.is_file():
        farm = Path(os.path.expanduser(os.environ.get("GROK_FARM_DIR") or "~/grok-farm"))
        env_path = farm / ".env"
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
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v
    except OSError:
        pass


_load_farm_env()


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
        headers={"Content-Type": "application/json", "User-Agent": "grok-farm-alerts/1.2"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 200 <= getattr(resp, "status", 200) < 300
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        print(f"[ALERT] post failed: {redact(e)}")
        return False


def format_email_list(
    emails: list[str] | set[str],
    *,
    limit: int = 40,
    html: bool = False,
) -> str:
    """Format emails for alert body (no tokens/passwords). Caps length.

    html=True wraps each address in <code> (caller must not double-escape).
    """
    items = sorted({str(e).strip() for e in emails if str(e).strip()})
    if not items:
        return "(none)" if not html else "<i>(none)</i>"
    shown = items[:limit]
    if html:
        lines = "\n".join(f"  • <code>{html_escape(e)}</code>" for e in shown)
    else:
        lines = "\n".join(f"  • {e}" for e in shown)
    if len(items) > limit:
        more = f"  … +{len(items) - limit} more"
        lines += f"\n{more}" if not html else f"\n  <i>… +{len(items) - limit} more</i>"
    return lines


def format_alert_plain(title: str, body: str, level: str) -> str:
    """Plain-text card (Discord-friendly markdown + Telegram fallback)."""
    emoji, label = LEVEL_META.get(level.lower(), ("ℹ️", level.upper()))
    header = f"{emoji} [{label}] Grok Farm — {title}"
    body = (body or "").strip()
    if not body:
        return header
    return f"{header}\n{'─' * 22}\n{body}"


def format_alert_html(
    title: str,
    body: str,
    level: str,
    extra: Optional[dict[str, Any]] = None,
) -> str:
    """
    Rich HTML card for Telegram parse_mode=HTML.
    Escapes all user content; never embeds JWT/password (caller must redact).
    """
    emoji, label = LEVEL_META.get(level.lower(), ("ℹ️", level.upper()))
    parts: list[str] = [
        f"<b>{emoji} {html_escape(label)} · Grok Farm</b>",
        f"<b>{html_escape(title)}</b>",
        "────────────────────",
    ]

    # Optional structured chips from extra (host / batch / counts)
    chips: list[str] = []
    if extra:
        for key in ("host", "batch_id", "created", "failed", "fail_class"):
            if key in extra and extra[key] is not None and str(extra[key]) != "":
                chips.append(
                    f"<b>{html_escape(key)}</b>=<code>{html_escape(str(extra[key])[:80])}</code>"
                )
    if chips:
        parts.append(" · ".join(chips))
        parts.append("")

    # Body lines: key=value → bold key + code value; section headers → bold; bullets → email in code
    # Multi-token lines like "created=2 failed=1 elapsed=90s" are split into chips
    kv_token_re = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)=([^\s]+)")
    section_re = re.compile(r"^[A-Za-z].*\):$|^[A-Za-z].*:$")
    for raw_line in (body or "").splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            parts.append("")
            continue
        stripped = line.strip()
        # Section headers from farm/workflow ("OK accounts (2):", "FAILED (1):")
        if section_re.match(stripped) and not stripped.startswith("•") and "=" not in stripped:
            parts.append(f"<b>{html_escape(stripped)}</b>")
            continue
        # Bullet lines (account lists / fail detail) — highlight first email in <code>
        if stripped.startswith("•") or stripped.startswith("…") or stripped.startswith("..."):
            indent = line[: len(line) - len(line.lstrip())]
            rest = stripped[1:].lstrip() if stripped.startswith("•") else stripped
            m_email = re.search(r"[\w.+-]+@[\w.-]+\.\w+", rest)
            if m_email:
                em = m_email.group(0)
                before, after = rest[: m_email.start()], rest[m_email.end() :]
                parts.append(
                    f"{indent}• {html_escape(before)}"
                    f"<code>{html_escape(em)}</code>"
                    f"{html_escape(after)}"
                )
            else:
                parts.append(f"{indent}• {html_escape(rest)}")
            continue
        # One or more key=value tokens on the line
        tokens = list(kv_token_re.finditer(stripped))
        if tokens and tokens[0].start() == 0:
            chips_line = " · ".join(
                f"<b>{html_escape(m.group(1))}</b>=<code>{html_escape(m.group(2)[:120])}</code>"
                for m in tokens
            )
            # Trailing free text after last token (if any)
            tail = stripped[tokens[-1].end() :].strip()
            if tail:
                chips_line += f" {html_escape(tail[:100])}"
            parts.append(chips_line)
            continue
        parts.append(html_escape(line))

    text = "\n".join(parts).strip()
    # Telegram limit 4096; leave headroom
    if len(text) > 4000:
        text = text[:3990] + "\n…"
    return text


def _send_telegram(text: str, *, parse_mode: Optional[str] = None) -> bool:
    """Send via Telegram Bot API when token + chat_id are set. Never logs token."""
    token = telegram_bot_token()
    chat_id = telegram_chat_id()
    if not token or not chat_id:
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": text[:4000],
        "disable_web_page_preview": True,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    ok = _post_json(url, payload)
    if not ok:
        print("[ALERT] telegram sendMessage failed (token/chat_id redacted)")
    return ok


def _send_telegram_rich(html_text: str, plain_text: str) -> bool:
    """Prefer HTML card; fall back to plain if Telegram rejects entities."""
    if _send_telegram(html_text, parse_mode="HTML"):
        return True
    # Retry without parse_mode (bad entities / unexpected tags)
    print("[ALERT] telegram HTML rejected — falling back to plain text")
    return _send_telegram(plain_text, parse_mode=None)


def _send_webhook(
    url: str,
    content: str,
    safe_title: str,
    safe_body: str,
    level: str,
    extra: Optional[dict[str, Any]],
    *,
    plain_text: str = "",
    html_text: str = "",
) -> bool:
    # Telegram Bot API URL form: https://api.telegram.org/botTOKEN/sendMessage
    if "api.telegram.org" in url and "sendMessage" in url:
        chat = telegram_chat_id() or ""
        try:
            parsed = urllib.parse.urlparse(url)
            qs = urllib.parse.parse_qs(parsed.query)
            if qs.get("chat_id"):
                chat = qs["chat_id"][0]
        except Exception:
            pass
        if not chat:
            print("[ALERT] telegram webhook URL missing chat_id")
            return False
        base = url.split("?")[0]
        # Prefer HTML via webhook path too
        if html_text:
            payload_html: dict[str, Any] = {
                "chat_id": chat,
                "text": html_text[:4000],
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            }
            if _post_json(base, payload_html):
                return True
        payload: dict[str, Any] = {
            "chat_id": chat,
            "text": (plain_text or content)[:4000],
            "disable_web_page_preview": True,
        }
        return _post_json(base, payload)

    # Discord-compatible: markdown content
    emoji, label = LEVEL_META.get(level.lower(), ("ℹ️", level.upper()))
    md = f"**{emoji} [{label}] Grok Farm — {safe_title}**\n{safe_body}".strip()
    payload = {
        "content": md[:1900],
        "title": safe_title,
        "body": safe_body,
        "level": level,
    }
    if extra:
        payload["extra"] = {k: redact(v) if isinstance(v, str) else v for k, v in extra.items()}
    return _post_json(url, payload)


def send_alert(
    title: str,
    body: str = "",
    level: str = "info",
    extra: Optional[dict[str, Any]] = None,
    *,
    skip_debounce: bool = False,
) -> bool:
    """
    Fire alert if Telegram (token+chat_id) and/or GROK_ALERT_WEBHOOK is set.
    Discord: markdown content with emoji level.
    Telegram: HTML card (parse_mode=HTML) with plain fallback.
    Never includes raw JWTs — body is redacted. Never logs bot token.
    Debounce: GROK_ALERT_DEBOUNCE_MIN (default 60) minutes per title+issues fingerprint.
    Per-batch farm/inject detail should pass skip_debounce=True so each batch notifies.
    """
    url = webhook_url()
    has_tg = bool(telegram_bot_token() and telegram_chat_id())
    if not url and not has_tg:
        return False
    if not skip_debounce and not _should_send(title, body, level, extra):
        return False
    safe_title = redact(title)[:200]
    # Larger body for account lists (Telegram hard-capped at 4000)
    safe_body = redact(body)[:3500]

    plain = format_alert_plain(safe_title, safe_body, level)
    html = format_alert_html(safe_title, safe_body, level, extra)

    sent = False
    if has_tg:
        sent = _send_telegram_rich(html, plain) or sent
    if url:
        sent = (
            _send_webhook(
                url,
                plain,
                safe_title,
                safe_body,
                level,
                extra,
                plain_text=plain,
                html_text=html,
            )
            or sent
        )
    if sent:
        print(f"[ALERT] sent level={level} title={safe_title[:80]}", flush=True)
    elif has_tg or url:
        print(f"[ALERT] failed level={level} title={safe_title[:80]}", flush=True)
    return sent
