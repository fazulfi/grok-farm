#!/usr/bin/env python3
"""Redact secrets from log strings (JWT, passwords, proxy user:pass)."""
from __future__ import annotations

import re
from typing import Any

# JWT-looking segments
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b")
# http(s)://user:pass@host
_PROXY_AUTH_RE = re.compile(
    r"(?i)\b((?:https?|socks5?h?)://)([^/\s:@]+):([^@/\s]+)@"
)
# common password / token key=value
_KV_SECRET_RE = re.compile(
    r"(?i)\b(password|passwd|pass|secret|api[_-]?key|access[_-]?token|refresh[_-]?token|"
    r"authorization|imap[_-]?pass|grok[_-]?password)\s*[:=]\s*([^\s,;\"']+)"
)
# Bearer tokens
_BEARER_RE = re.compile(r"(?i)\b(bearer\s+)([A-Za-z0-9_\-\.=]+)")


def redact_proxy_url(url: str, keep: int = 40) -> str:
    """Show scheme+host prefix only; strip user:pass."""
    if not url:
        return ""
    safe = _PROXY_AUTH_RE.sub(r"\1***:***@", str(url))
    if len(safe) > keep:
        return safe[:keep] + "..."
    return safe


def redact(text: Any) -> str:
    """Redact JWTs, proxy credentials, and common secret key=value pairs."""
    if text is None:
        return ""
    s = str(text)
    s = _JWT_RE.sub("eyJ[REDACTED]", s)
    s = _PROXY_AUTH_RE.sub(r"\1***:***@", s)
    s = _BEARER_RE.sub(r"\1[REDACTED]", s)
    s = _KV_SECRET_RE.sub(r"\1=[REDACTED]", s)
    return s


def safe_print(*args: Any, **kwargs: Any) -> None:
    """print() with redaction applied to all stringified args."""
    print(*(redact(a) for a in args), **kwargs)
