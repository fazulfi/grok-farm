#!/usr/bin/env python3
"""Optional webhook / Telegram alerts (Discord-compatible + Bot API HTML cards + effects)."""
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

# Telegram private-chat message_effect_id (animated full-screen effect on send).
# Only FREE effects (no Telegram Premium on recipient). Probed 2026-07-14 against
# chat 1812698903: party/fire/thumbs/poop = OK; sparkle/lightning/boom/trophy/etc =
# PREMIUM_ACCOUNT_REQUIRED.
LEVEL_EFFECT: dict[str, str] = {
    "success": "5046509860389126442",  # 🎉 party
    "info": "5107584321108051014",  # 👍 thumbs
    "warning": "5104841245755180586",  # 🔥 fire
    "critical": "5104841245755180586",  # 🔥 fire
    "error": "5104841245755180586",  # 🔥 fire
    "debug": "5107584321108051014",  # 👍 thumbs
}

# Section header emoji banners (animation-feel without CSS)
SECTION_EMOJI = {
    "ok": "✅",
    "success": "✅",
    "fail": "❌",
    "failed": "❌",
    "error": "❌",
    "warn": "⚠️",
    "warning": "⚠️",
    "host": "🖥️",
    "batch": "📦",
    "proxy": "🌐",
    "inject": "💉",
    "farm": "🌾",
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


def _effects_enabled() -> bool:
    """GROK_TELEGRAM_EFFECTS=0|false|off disables message_effect_id."""
    raw = (os.environ.get("GROK_TELEGRAM_EFFECTS") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _effect_for_level(level: str) -> Optional[str]:
    if not _effects_enabled():
        return None
    return LEVEL_EFFECT.get((level or "info").lower())


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


def _post_json(
    url: str,
    payload: dict[str, Any],
    timeout: float = 15,
) -> tuple[bool, Optional[dict[str, Any]]]:
    """POST JSON to Telegram/Discord. Returns (ok, parsed_body_or_None).

    Telegram sendMessage/editMessageText bodies include result.message_id.
    Never logs bot tokens.
    """
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "User-Agent": "grok-farm-alerts/1.4"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = getattr(resp, "status", 200)
            raw = resp.read().decode("utf-8", errors="replace")
            body: Optional[dict[str, Any]] = None
            try:
                parsed = json.loads(raw) if raw.strip() else None
                if isinstance(parsed, dict):
                    body = parsed
            except json.JSONDecodeError:
                body = None
            ok = 200 <= status < 300
            if body is not None and body.get("ok") is False:
                ok = False
            return ok, body
    except (urllib.error.URLError, TimeoutError, OSError, urllib.error.HTTPError) as e:
        # Try to read Telegram error JSON from HTTPError
        body = None
        if isinstance(e, urllib.error.HTTPError):
            try:
                raw = e.read().decode("utf-8", errors="replace")
                parsed = json.loads(raw) if raw.strip() else None
                if isinstance(parsed, dict):
                    body = parsed
            except Exception:
                pass
        print(f"[ALERT] post failed: {redact(e)}")
        return False, body


def _post_json_ok(url: str, payload: dict[str, Any], timeout: float = 15) -> bool:
    """Boolean wrapper for callers that only need success/fail."""
    ok, _ = _post_json(url, payload, timeout=timeout)
    return ok


def sticky_state_path(name: str = "fleet_digest") -> Path:
    """Path for sticky Telegram message_id state (never git)."""
    safe = re.sub(r"[^a-zA-Z0-9._-]+", "_", (name or "fleet_digest").strip()) or "fleet"
    base = Path(
        os.path.expanduser(
            os.environ.get("GROK_TELEGRAM_STICKY_DIR")
            or "~/.config/grok-farm"
        )
    )
    return base / f"telegram_sticky_{safe}.json"


def load_sticky_message_id(name: str = "fleet_digest") -> Optional[int]:
    path = sticky_state_path(name)
    try:
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        mid = data.get("message_id")
        if mid is None:
            return None
        return int(mid)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def save_sticky_message_id(
    message_id: int,
    name: str = "fleet_digest",
    *,
    chat_id: str = "",
) -> None:
    path = sticky_state_path(name)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "message_id": int(message_id),
            "chat_id": str(chat_id or telegram_chat_id() or ""),
            "updated_at": int(time.time()),
            "name": name,
        }
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    except OSError as e:
        print(f"[ALERT] sticky state save failed: {redact(e)}", flush=True)


def clear_sticky_message_id(name: str = "fleet_digest") -> None:
    path = sticky_state_path(name)
    try:
        if path.is_file():
            path.unlink()
    except OSError:
        pass


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


def _status_bar(ok: int, fail: int, width: int = 10) -> str:
    """Unicode progress bar for ok vs fail counts (visual, not animated)."""
    total = max(0, int(ok)) + max(0, int(fail))
    if total <= 0:
        return "░" * width
    filled = min(width, max(0, round(width * int(ok) / total)))
    return "█" * filled + "░" * (width - filled)


def format_alert_plain(title: str, body: str, level: str) -> str:
    """Plain-text card (Discord-friendly markdown + Telegram fallback)."""
    emoji, label = LEVEL_META.get(level.lower(), ("ℹ️", level.upper()))
    header = f"{emoji} [{label}] Grok Farm — {title}"
    body = (body or "").strip()
    if not body:
        return header
    return f"{header}\n{'─' * 22}\n{body}"


def _section_banner(stripped: str) -> Optional[str]:
    """Map section headers like 'OK accounts (2):' to emoji banner line."""
    low = stripped.lower()
    for key, emo in SECTION_EMOJI.items():
        if low.startswith(key) or f" {key}" in low[:24]:
            return f"{emo} <b>{html_escape(stripped)}</b>"
    # Generic section ending with :
    if stripped.endswith(":") and not stripped.startswith("•"):
        return f"▸ <b>{html_escape(stripped)}</b>"
    return None


def format_alert_html(
    title: str,
    body: str,
    level: str,
    extra: Optional[dict[str, Any]] = None,
) -> str:
    """
    Rich HTML card for Telegram parse_mode=HTML.
    Escapes all user content; never embeds JWT/password (caller must redact).
    Uses blockquote meta strip + section banners + optional status bar for denser UX.
    """
    emoji, label = LEVEL_META.get(level.lower(), ("ℹ️", level.upper()))
    parts: list[str] = [
        f"<b>{emoji} {html_escape(label)} · Grok Farm</b>",
        f"<b>{html_escape(title)}</b>",
    ]

    # Meta strip as blockquote (Telegram renders with left accent bar — more “UI”)
    meta_lines: list[str] = []
    if extra:
        for key, icon in (
            ("host", "🖥️"),
            ("batch_id", "📦"),
            ("created", "✅"),
            ("failed", "❌"),
            ("fail_class", "🏷️"),
            ("ok", "✅"),
            ("fail", "❌"),
        ):
            if key in extra and extra[key] is not None and str(extra[key]) != "":
                meta_lines.append(
                    f"{icon} <b>{html_escape(key)}</b> "
                    f"<code>{html_escape(str(extra[key])[:80])}</code>"
                )
        # Status bar when we have ok/fail or created/failed counts
        try:
            ok_n = int(extra.get("ok") or extra.get("created") or 0)
            fail_n = int(extra.get("fail") or extra.get("failed") or 0)
            if ok_n or fail_n:
                bar = _status_bar(ok_n, fail_n)
                meta_lines.append(
                    f"📊 <code>{bar}</code> "
                    f"<b>{ok_n}</b> ok · <b>{fail_n}</b> fail"
                )
        except (TypeError, ValueError):
            pass
    if meta_lines:
        # Telegram HTML supports <blockquote> (Bot API 7.0+)
        parts.append("<blockquote>" + "\n".join(meta_lines) + "</blockquote>")
    else:
        parts.append("────────────────────")

    # Body lines: key=value → bold key + code value; section headers → banner; bullets → email in code
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
            banner = _section_banner(stripped)
            parts.append(banner or f"<b>{html_escape(stripped)}</b>")
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


def _extract_message_id(body: Optional[dict[str, Any]]) -> Optional[int]:
    if not body or not isinstance(body, dict):
        return None
    result = body.get("result")
    if isinstance(result, dict) and result.get("message_id") is not None:
        try:
            return int(result["message_id"])
        except (TypeError, ValueError):
            return None
    return None


def _send_telegram(
    text: str,
    *,
    parse_mode: Optional[str] = None,
    message_effect_id: Optional[str] = None,
) -> tuple[bool, Optional[int]]:
    """Send via Telegram Bot API. Returns (ok, message_id). Never logs token."""
    token = telegram_bot_token()
    chat_id = telegram_chat_id()
    if not token or not chat_id:
        return False, None
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": text[:4000],
        "disable_web_page_preview": True,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if message_effect_id:
        payload["message_effect_id"] = str(message_effect_id)
    ok, body = _post_json(url, payload)
    mid = _extract_message_id(body) if ok else None
    if not ok and message_effect_id:
        # Effects only work in private chats; retry without effect if rejected
        payload.pop("message_effect_id", None)
        ok, body = _post_json(url, payload)
        mid = _extract_message_id(body) if ok else None
        if ok:
            print("[ALERT] telegram effect unsupported — sent without effect", flush=True)
            return True, mid
    if not ok:
        print("[ALERT] telegram sendMessage failed (token/chat_id redacted)")
    return ok, mid


def _edit_telegram(
    message_id: int,
    text: str,
    *,
    parse_mode: Optional[str] = None,
) -> bool:
    """editMessageText for sticky live-updating cards. No message_effect on edit."""
    token = telegram_bot_token()
    chat_id = telegram_chat_id()
    if not token or not chat_id or not message_id:
        return False
    url = f"https://api.telegram.org/bot{token}/editMessageText"
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "message_id": int(message_id),
        "text": text[:4000],
        "disable_web_page_preview": True,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    ok, body = _post_json(url, payload)
    if ok:
        return True
    # "message is not modified" is success for sticky updates
    desc = ""
    if body and isinstance(body, dict):
        desc = str(body.get("description") or "").lower()
    if "message is not modified" in desc:
        return True
    return False


def _send_telegram_rich(
    html_text: str,
    plain_text: str,
    *,
    level: str = "info",
) -> tuple[bool, Optional[int]]:
    """Prefer HTML card + effect; fall back to plain / no-effect if Telegram rejects."""
    effect = _effect_for_level(level)
    ok, mid = _send_telegram(html_text, parse_mode="HTML", message_effect_id=effect)
    if ok:
        return True, mid
    # Retry HTML without effect
    if effect:
        ok, mid = _send_telegram(html_text, parse_mode="HTML", message_effect_id=None)
        if ok:
            print("[ALERT] telegram HTML ok without effect", flush=True)
            return True, mid
    # Retry without parse_mode (bad entities / unexpected tags)
    print("[ALERT] telegram HTML rejected — falling back to plain text")
    return _send_telegram(plain_text, parse_mode=None, message_effect_id=None)


def send_or_edit_sticky(
    title: str,
    body: str = "",
    level: str = "info",
    extra: Optional[dict[str, Any]] = None,
    *,
    sticky_name: str = "fleet_digest",
    force_new: bool = False,
) -> bool:
    """Send or edit a single sticky Telegram message (live-updating card).

    Used by fleet daily digest so the bot keeps one message that updates
    instead of spamming a new card each run. Falls back to sendMessage if
    no saved message_id or edit fails (message deleted / too old).
    Effects only on brand-new send (Telegram cannot animate edits).
    """
    has_tg = bool(telegram_bot_token() and telegram_chat_id())
    if not has_tg:
        # Fallback: normal alert path (webhook may still work)
        return send_alert(title, body, level=level, extra=extra, skip_debounce=True)

    safe_title = redact(title)[:200]
    safe_body = redact(body)[:3500]
    plain = format_alert_plain(safe_title, safe_body, level)
    html = format_alert_html(safe_title, safe_body, level, extra)

    mid: Optional[int] = None if force_new else load_sticky_message_id(sticky_name)
    if mid is not None:
        if _edit_telegram(mid, html, parse_mode="HTML"):
            print(
                f"[ALERT] sticky edited mid={mid} level={level} title={safe_title[:60]}",
                flush=True,
            )
            save_sticky_message_id(mid, sticky_name)
            return True
        if _edit_telegram(mid, plain, parse_mode=None):
            print(
                f"[ALERT] sticky edited plain mid={mid} level={level}",
                flush=True,
            )
            save_sticky_message_id(mid, sticky_name)
            return True
        print(
            f"[ALERT] sticky edit failed mid={mid} — sending new message",
            flush=True,
        )
        clear_sticky_message_id(sticky_name)

    ok, new_mid = _send_telegram_rich(html, plain, level=level)
    if ok and new_mid is not None:
        save_sticky_message_id(new_mid, sticky_name)
        print(
            f"[ALERT] sticky sent mid={new_mid} level={level} title={safe_title[:60]}",
            flush=True,
        )
    elif ok:
        print(f"[ALERT] sticky sent (no mid) level={level}", flush=True)
    else:
        print(f"[ALERT] sticky failed level={level} title={safe_title[:60]}", flush=True)
    return ok


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
        effect = _effect_for_level(level)
        # Prefer HTML via webhook path too
        if html_text:
            payload_html: dict[str, Any] = {
                "chat_id": chat,
                "text": html_text[:4000],
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            }
            if effect:
                payload_html["message_effect_id"] = effect
            if _post_json_ok(base, payload_html):
                return True
            if effect:
                payload_html.pop("message_effect_id", None)
                if _post_json_ok(base, payload_html):
                    return True
        payload: dict[str, Any] = {
            "chat_id": chat,
            "text": (plain_text or content)[:4000],
            "disable_web_page_preview": True,
        }
        return _post_json_ok(base, payload)

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
    return _post_json_ok(url, payload)


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
    Telegram: HTML card (parse_mode=HTML) + optional message_effect_id animation,
    plain fallback. Never includes raw JWTs — body is redacted. Never logs bot token.
    Debounce: GROK_ALERT_DEBOUNCE_MIN (default 60) minutes per title+issues fingerprint.
    Per-batch farm/inject detail should pass skip_debounce=True so each batch notifies.
    Effects: GROK_TELEGRAM_EFFECTS=0 disables private-chat animation effects.
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
        ok_tg, _mid = _send_telegram_rich(html, plain, level=level)
        sent = ok_tg or sent
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
