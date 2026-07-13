#!/usr/bin/env python3
"""Multi-domain / multi-IMAP identity pool for Grok Farm.

Enterprise identity plane:
  - Domain pool: GROK_EMAIL_DOMAINS or legacy GROK_EMAIL_DOMAIN
  - Optional identity file maps domain → IMAP inbox (multi Gmail)
  - Default: all catch-all domains share GROK_IMAP_* credentials

See docs/ARCHITECTURE.md § identity plane and docs/OPERATIONS.md multi-domain workflow.
"""
from __future__ import annotations

import json
import os
import random
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def _env(key: str, default: str = "") -> str:
    return (os.environ.get(key) or default).strip()


def _norm_domain(d: str) -> str:
    d = (d or "").strip().lower().lstrip("@")
    # strip accidental paths / schemes
    if "/" in d:
        d = d.split("/")[0]
    return d


def parse_domain_list(raw: str) -> list[str]:
    """Parse comma/space/semicolon separated domains; de-dupe preserve order."""
    if not raw:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for part in raw.replace(";", ",").replace(" ", ",").split(","):
        d = _norm_domain(part)
        if not d or d in seen:
            continue
        seen.add(d)
        out.append(d)
    return out


@dataclass(frozen=True)
class ImapCreds:
    user: str
    password: str
    host: str = "imap.gmail.com"
    port: int = 993

    def label(self) -> str:
        return f"{self.user}@{self.host}:{self.port}"


@dataclass
class Identity:
    """One OTP inbox that may own one or more catch-all domains."""

    id: str
    imap: ImapCreds
    domains: list[str] = field(default_factory=list)
    gmail_base: str = ""  # plus_trick base for this identity
    weight: int = 1
    enabled: bool = True

    def owns_domain(self, domain: str) -> bool:
        return _norm_domain(domain) in {_norm_domain(d) for d in self.domains}


@dataclass
class IdentityPool:
    mode: str  # domain | plus_trick
    strategy: str  # random | round_robin
    domains: list[str]
    identities: list[Identity]
    default_imap: ImapCreds
    source: str = "env"

    _rr_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _rr_idx: int = field(default=0, repr=False)

    def domain_list(self) -> list[str]:
        return list(self.domains)

    def pick_domain(self, skip_domains: set[str] | None = None) -> str:
        """Pick a domain from the pool.

        skip_domains: unhealthy / disabled domains (from domain_stats). If all
        candidates would be skipped, fail-open and pick from full pool so farming
        never hard-stops when catch-all is flaky.

        Strategies:
          - round_robin: strict alternation across healthy candidates (thread-safe)
          - random: weighted random (identity.weight) among healthy candidates
        """
        if not self.domains:
            raise RuntimeError(
                "No email domains configured — set GROK_EMAIL_DOMAINS or GROK_EMAIL_DOMAIN"
            )
        skip = {_norm_domain(d) for d in (skip_domains or set()) if d}
        candidates = [d for d in self.domains if _norm_domain(d) not in skip]
        if not candidates:
            # fail-open: keep farming rather than hard-stop on flaky catch-all
            candidates = list(self.domains)
        if len(candidates) == 1:
            return candidates[0]
        if self.strategy == "round_robin":
            with self._rr_lock:
                # Rotate only among healthy candidates (stable order = domains list order)
                n = len(candidates)
                idx = self._rr_idx % n
                self._rr_idx += 1
                return candidates[idx]
        # weighted via identity weights when multi-identity; else uniform random
        weights = []
        for d in candidates:
            idn = self.identity_for_domain(d)
            weights.append(max(1, idn.weight if idn else 1))
        return random.choices(candidates, weights=weights, k=1)[0]

    def identity_for_domain(self, domain: str) -> Identity | None:
        domain = _norm_domain(domain)
        for ident in self.identities:
            if not ident.enabled:
                continue
            if ident.owns_domain(domain):
                return ident
        # fallback: first enabled identity that has no domain restriction (global)
        for ident in self.identities:
            if ident.enabled and not ident.domains:
                return ident
        return self.identities[0] if self.identities else None

    def imap_for_email(self, email: str) -> ImapCreds:
        if "@" not in (email or ""):
            return self.default_imap
        domain = email.rsplit("@", 1)[-1]
        ident = self.identity_for_domain(domain)
        return ident.imap if ident else self.default_imap

    def gmail_base_for_plus(self) -> str:
        for ident in self.identities:
            if ident.enabled and ident.gmail_base:
                return ident.gmail_base.lower()
        return (self.default_imap.user or "").lower()

    def summary(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "strategy": self.strategy,
            "domains": self.domains,
            "domain_count": len(self.domains),
            "identity_count": len(self.identities),
            "identities": [
                {
                    "id": i.id,
                    "imap_user": i.imap.user,
                    "imap_host": i.imap.host,
                    "domains": i.domains,
                    "weight": i.weight,
                    "enabled": i.enabled,
                }
                for i in self.identities
            ],
            "source": self.source,
        }


def _load_identity_file(path: Path, default_imap: ImapCreds) -> list[Identity] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise RuntimeError(f"Invalid identity file {path}: {e}") from e

    raw_list = data
    if isinstance(data, dict):
        raw_list = data.get("identities") or data.get("pool") or []
    if not isinstance(raw_list, list) or not raw_list:
        raise RuntimeError(f"Identity file {path} must contain a non-empty list")

    out: list[Identity] = []
    for idx, item in enumerate(raw_list):
        if not isinstance(item, dict):
            continue
        if item.get("enabled") is False:
            continue
        user = (item.get("imap_user") or item.get("user") or default_imap.user or "").strip()
        password = (
            item.get("imap_pass")
            or item.get("password")
            or item.get("imap_password")
            or default_imap.password
            or ""
        )
        password = str(password).replace(" ", "")
        host = (item.get("imap_host") or item.get("host") or default_imap.host or "imap.gmail.com").strip()
        port = int(item.get("imap_port") or item.get("port") or default_imap.port or 993)
        domains = parse_domain_list(
            ",".join(item.get("domains") or [])
            if isinstance(item.get("domains"), list)
            else str(item.get("domains") or item.get("domain") or "")
        )
        iid = str(item.get("id") or item.get("name") or f"id{idx}")
        weight = max(1, int(item.get("weight") or 1))
        gmail_base = (item.get("gmail_base") or user or "").lower()
        if not user or not password:
            raise RuntimeError(f"Identity {iid}: imap_user and imap_pass required")
        out.append(
            Identity(
                id=iid,
                imap=ImapCreds(user=user, password=password, host=host, port=port),
                domains=domains,
                gmail_base=gmail_base,
                weight=weight,
                enabled=True,
            )
        )
    return out or None


def load_identity_pool(root: Path | str | None = None) -> IdentityPool:
    """Build pool from env + optional GROK_IDENTITY_FILE.

    Precedence for domains:
      1. GROK_EMAIL_DOMAINS (comma list)
      2. domains union from identity file
      3. GROK_EMAIL_DOMAIN (legacy single)
    """
    if root is None:
        root = Path(os.path.expanduser(os.environ.get("GROK_FARM_DIR") or ".")).resolve()
    else:
        root = Path(root).expanduser().resolve()
    mode = _env("GROK_EMAIL_MODE", "domain").lower()
    if mode not in ("domain", "plus_trick"):
        mode = "domain"
    strategy = _env("GROK_EMAIL_DOMAIN_STRATEGY", "random").lower()
    if strategy not in ("random", "round_robin"):
        strategy = "random"

    default_imap = ImapCreds(
        user=_env("GROK_IMAP_USER"),
        password=_env("GROK_IMAP_PASS").replace(" ", ""),
        host=_env("GROK_IMAP_HOST", "imap.gmail.com") or "imap.gmail.com",
        port=int(_env("GROK_IMAP_PORT", "993") or "993"),
    )

    id_path_raw = _env("GROK_IDENTITY_FILE")
    id_path = Path(os.path.expanduser(id_path_raw)) if id_path_raw else root / "identities.json"
    file_idents: list[Identity] | None = None
    source = "env"
    if id_path.is_file():
        file_idents = _load_identity_file(id_path, default_imap)
        source = f"file:{id_path}"

    env_domains = parse_domain_list(_env("GROK_EMAIL_DOMAINS"))
    legacy = parse_domain_list(_env("GROK_EMAIL_DOMAIN"))
    file_domains: list[str] = []
    if file_idents:
        for ident in file_idents:
            for d in ident.domains:
                if d not in file_domains:
                    file_domains.append(d)

    domains: list[str] = []
    for d in env_domains + file_domains + legacy:
        if d not in domains:
            domains.append(d)

    if file_idents:
        identities = file_idents
        # if identity has empty domains list, attach all pool domains to first
        if domains and all(not i.domains for i in identities):
            identities[0].domains = list(domains)
    else:
        # single default identity owning all domains
        gmail_base = _env("GROK_GMAIL_BASE").lower() or default_imap.user.lower()
        identities = [
            Identity(
                id="default",
                imap=default_imap,
                domains=list(domains),
                gmail_base=gmail_base,
                weight=1,
            )
        ]

    return IdentityPool(
        mode=mode,
        strategy=strategy,
        domains=domains,
        identities=identities,
        default_imap=default_imap,
        source=source,
    )


def domain_counts_from_emails(emails: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for e in emails:
        if not e or "@" not in e:
            continue
        d = e.rsplit("@", 1)[-1].lower()
        counts[d] = counts.get(d, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))
