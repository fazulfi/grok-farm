#!/usr/bin/env python3
"""Realistic human-style email local-parts + display names.

Styles (GROK_EMAIL_LOCAL_STYLE):
  crypto     — alnum secrets (legacy hash-like)
  realistic  — offline first/last name patterns (default)
  parser     — parser.name API when GROK_PARSER_NAME_API_KEY set; falls back to realistic

Name corpora (data/*.txt — more files = more variety):
  names_id.txt          — Indonesian names (gist maulvi/nama.txt cleaned)
  names_en_first.txt    — English given names
  names_en_last.txt     — English surnames
  names_intl_first.txt  — international given names
  Optional operator: names_extra_first.txt / names_extra_last.txt
  Or GROK_NAME_FIRST_FILES / GROK_NAME_LAST_FILES (comma paths)

GROK_NAME_REGION=mixed|id|en|intl  (default mixed)

parser.name free tier ~100 req/day — offline corpus is primary for unlimited farm.
"""
from __future__ import annotations

import json
import os
import random
import re
import secrets
import string
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_ALPHANUM = string.ascii_lowercase + string.digits
_LOCK = threading.Lock()
_PARSER_CACHE: list[dict[str, str]] = []
_PARSER_LAST_ERR: str = ""
_PARSER_LAST_FETCH: float = 0.0
_DATA_DIR = Path(__file__).resolve().parent / "data"

_FALLBACK_FIRST = [
    "James", "John", "Robert", "Michael", "William", "David", "Mary", "Jennifer",
    "Linda", "Elizabeth", "Alex", "Jordan", "Taylor", "Morgan", "Casey", "Riley",
    "Noah", "Liam", "Emma", "Olivia", "Mia", "Lucas", "Mason", "Sophia", "Budi",
    "Agus", "Dewi", "Siti", "Putri", "Rizky", "Ayu", "Andi", "Fitri", "Rina",
]
_FALLBACK_LAST = [
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis",
    "Wilson", "Anderson", "Thomas", "Taylor", "Moore", "Jackson", "Martin", "Lee",
    "Santoso", "Wijaya", "Pratama", "Saputra", "Hidayat", "Kurniawan", "Putra",
    "Sari", "Lestari", "Wulandari", "Nugroho", "Setiawan", "Rahman", "Maulana",
]


def _env(key: str, default: str = "") -> str:
    return (os.environ.get(key) or default).strip()


def _title_name(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return s
    return s[:1].upper() + s[1:].lower()


def _load_name_file(path: Path) -> list[str]:
    if not path.is_file():
        return []
    out: list[str] = []
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            w = line.strip()
            if not w or w.startswith("#"):
                continue
            w = re.sub(r"\s+", " ", w)
            if "." in w and len(w) <= 4:
                continue
            if len(w) < 2:
                continue
            out.append(_title_name(w))
    except OSError:
        return []
    seen: set[str] = set()
    uniq: list[str] = []
    for n in out:
        k = n.lower()
        if k in seen:
            continue
        seen.add(k)
        uniq.append(n)
    return uniq


def _merge(*lists: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for lst in lists:
        for n in lst:
            k = n.lower()
            if k in seen:
                continue
            seen.add(k)
            out.append(n)
    return out


def _load_corpora() -> dict[str, list[str]]:
    id_names = _load_name_file(_DATA_DIR / "names_id.txt")
    en_first = _load_name_file(_DATA_DIR / "names_en_first.txt")
    en_last = _load_name_file(_DATA_DIR / "names_en_last.txt")
    intl = _load_name_file(_DATA_DIR / "names_intl_first.txt")
    extra_f = _load_name_file(_DATA_DIR / "names_extra_first.txt")
    extra_l = _load_name_file(_DATA_DIR / "names_extra_last.txt")

    for key, bucket in (
        ("GROK_NAME_FIRST_FILES", "extra_first"),
        ("GROK_NAME_LAST_FILES", "extra_last"),
    ):
        raw = _env(key)
        if not raw:
            continue
        for part in re.split(r"[,;]", raw):
            part = part.strip()
            if not part:
                continue
            path = Path(os.path.expanduser(part))
            if not path.is_file():
                path = _DATA_DIR / part
            loaded = _load_name_file(path)
            if bucket == "extra_first":
                extra_f = _merge(extra_f, loaded)
            else:
                extra_l = _merge(extra_l, loaded)

    last_id = [n for n in id_names if len(n) >= 4]
    first_all = _merge(en_first, id_names, intl, extra_f) or list(_FALLBACK_FIRST)
    last_all = _merge(en_last, last_id, extra_l) or list(_FALLBACK_LAST)

    return {
        "id": id_names or list(_FALLBACK_FIRST),
        "en_first": en_first or list(_FALLBACK_FIRST),
        "en_last": en_last or list(_FALLBACK_LAST),
        "intl": intl or list(_FALLBACK_FIRST),
        "extra_first": extra_f,
        "extra_last": extra_l,
        "first_all": first_all,
        "last_all": last_all,
        "first_id": id_names or list(_FALLBACK_FIRST),
        "last_id": last_id or list(_FALLBACK_LAST),
    }


_CORPUS = _load_corpora()
FIRST_NAMES = list(_CORPUS["first_all"])
LAST_NAMES = list(_CORPUS["last_all"])


def corpus_stats() -> dict[str, Any]:
    return {
        "data_dir": str(_DATA_DIR),
        "id": len(_CORPUS["id"]),
        "en_first": len(_CORPUS["en_first"]),
        "en_last": len(_CORPUS["en_last"]),
        "intl": len(_CORPUS["intl"]),
        "extra_first": len(_CORPUS["extra_first"]),
        "extra_last": len(_CORPUS["extra_last"]),
        "first_all": len(FIRST_NAMES),
        "last_all": len(LAST_NAMES),
        "region": (_env("GROK_NAME_REGION", "mixed") or "mixed").lower(),
    }


def _ascii_slug(s: str) -> str:
    s = (s or "").strip().lower()
    repl = str.maketrans(
        {
            "á": "a", "à": "a", "ä": "a", "â": "a", "ã": "a",
            "é": "e", "è": "e", "ë": "e", "ê": "e",
            "í": "i", "ì": "i", "ï": "i", "î": "i",
            "ó": "o", "ò": "o", "ö": "o", "ô": "o", "õ": "o",
            "ú": "u", "ù": "u", "ü": "u", "û": "u",
            "ñ": "n", "ç": "c", "ß": "ss",
        }
    )
    s = s.translate(repl)
    return re.sub(r"[^a-z0-9]+", "", s)


@dataclass(frozen=True)
class PersonName:
    first: str
    last: str
    source: str = "local"

    @property
    def display_first(self) -> str:
        return self.first

    @property
    def display_last(self) -> str:
        return self.last


def crypto_local_part(length: int = 16) -> str:
    length = max(6, min(32, int(length)))
    return "".join(secrets.choice(_ALPHANUM) for _ in range(length))


def _region() -> str:
    r = (_env("GROK_NAME_REGION", "mixed") or "mixed").lower()
    if r in ("id", "indonesia", "indo"):
        return "id"
    if r in ("en", "us", "uk", "english"):
        return "en"
    if r in ("intl", "international", "global"):
        return "intl"
    return "mixed"


def pick_local_person() -> PersonName:
    reg = _region()
    c = _CORPUS
    if reg == "id":
        first = random.choice(c["first_id"])
        last = random.choice(c["last_id"])
        if last.lower() == first.lower() and len(c["last_id"]) > 1:
            last = random.choice(c["last_id"])
    elif reg == "en":
        first = random.choice(c["en_first"] or c["first_all"])
        last = random.choice(c["en_last"] or c["last_all"])
    elif reg == "intl":
        first = random.choice(c["intl"] or c["first_all"])
        last = random.choice(c["en_last"] or c["last_all"])
    else:
        roll = secrets.randbelow(100)
        if roll < 45 and c["first_id"]:
            first = random.choice(c["first_id"])
            last = random.choice(
                c["last_id"] if secrets.randbelow(100) < 55 else (c["en_last"] or c["last_all"])
            )
        elif roll < 85 and c["en_first"]:
            first = random.choice(c["en_first"])
            last = random.choice(c["en_last"] or c["last_all"])
        else:
            first = random.choice(c["intl"] or c["first_all"])
            last = random.choice(c["en_last"] or c["last_all"])
        if last.lower() == first.lower():
            last = random.choice(c["last_all"])
    return PersonName(first=first, last=last, source="local")


def _year_suffix() -> str:
    if secrets.randbelow(100) < 55:
        return f"{secrets.randbelow(30) + 70:02d}"
    if secrets.randbelow(100) < 50:
        return f"{secrets.randbelow(26):02d}"
    return str(secrets.randbelow(900) + 100)


def realistic_local_part(person: PersonName | None = None) -> tuple[str, PersonName]:
    p = person or pick_local_person()
    f = _ascii_slug(p.first)
    l = _ascii_slug(p.last)
    if not f or not l:
        f, l = "alex", "smith"
    fi, li = f[0], l[0]
    yy = _year_suffix()
    n2 = f"{secrets.randbelow(90) + 10}"
    n1 = str(secrets.randbelow(9) + 1)
    patterns = [
        (20, f"{f}.{l}"),
        (14, f"{f}{l}"),
        (12, f"{f}_{l}"),
        (12, f"{fi}.{l}"),
        (10, f"{f}.{l}{n2}"),
        (9, f"{f}{l}{n2}"),
        (8, f"{f}.{l}.{yy}"),
        (7, f"{f}{yy}"),
        (6, f"{fi}{l}{n2}"),
        (5, f"{f}-{l}"),
        (5, f"{f}.{li}{n2}"),
        (4, f"{f}.{l}{n1}"),
        (4, f"{l}.{f}"),
        (3, f"{f}{li}{yy}"),
        (2, f"{fi}.{l}.{yy}"),
    ]
    weights = [w for w, _ in patterns]
    choice = random.choices([x for _, x in patterns], weights=weights, k=1)[0]
    if len(choice) > 48:
        choice = f"{f}{l}{n2}"[:48]
    if len(choice) < 5:
        choice = f"{f}.{l}{n2}"
    return choice, p


def _parser_api_key() -> str:
    return _env("GROK_PARSER_NAME_API_KEY") or _env("PARSER_NAME_API_KEY")


def _parser_country() -> str:
    return (_env("GROK_PARSER_NAME_COUNTRY", "US") or "US").upper()[:2]


def _parser_fetch_batch(n: int = 10) -> list[dict[str, str]]:
    global _PARSER_LAST_ERR, _PARSER_LAST_FETCH
    key = _parser_api_key()
    if not key:
        return []
    results = max(1, min(25, n))
    params = {
        "api_key": key,
        "endpoint": "generate",
        "results": str(results),
        "country_code": _parser_country(),
    }
    url = "https://api.parser.name/?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "grok-farm-name-gen/2.1", "Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            body = resp.read().decode("utf-8", errors="replace")
        data = json.loads(body)
        if data.get("error"):
            _PARSER_LAST_ERR = str(data["error"])
            return []
        out: list[dict[str, str]] = []
        for row in data.get("data") or []:
            if not isinstance(row, dict):
                continue
            name = row.get("name") or {}
            fn = (
                (name.get("firstname") or {}).get("name_ascii")
                or (name.get("firstname") or {}).get("name")
                or ""
            )
            ln = (
                (name.get("lastname") or {}).get("name_ascii")
                or (name.get("lastname") or {}).get("name")
                or ""
            )
            email = row.get("email") or {}
            local = (email.get("username") or "").strip().lower()
            if not fn or not ln:
                continue
            if not local or not re.match(r"^[a-z0-9._+-]+$", local):
                local, _ = realistic_local_part(PersonName(fn, ln, "parser"))
            out.append({"first": fn, "last": ln, "local": local})
        _PARSER_LAST_ERR = ""
        _PARSER_LAST_FETCH = time.time()
        return out
    except (
        urllib.error.URLError,
        urllib.error.HTTPError,
        TimeoutError,
        json.JSONDecodeError,
        OSError,
    ) as e:
        _PARSER_LAST_ERR = str(e)
        return []


def _parser_take_one() -> tuple[str, PersonName] | None:
    global _PARSER_CACHE
    with _LOCK:
        if not _PARSER_CACHE:
            if time.time() - _PARSER_LAST_FETCH < 2.0 and _PARSER_LAST_ERR:
                return None
            batch = _parser_fetch_batch(int(_env("GROK_PARSER_NAME_BATCH", "5") or "5"))
            _PARSER_CACHE.extend(batch)
        if not _PARSER_CACHE:
            return None
        row = _PARSER_CACHE.pop(0)
    p = PersonName(row["first"], row["last"], source="parser")
    local = row.get("local") or realistic_local_part(p)[0]
    return local, p


def resolve_style(raw: str | None = None) -> str:
    s = (raw if raw is not None else _env("GROK_EMAIL_LOCAL_STYLE", "realistic")).lower()
    if s in ("hash", "crypto", "random", "alnum"):
        return "crypto"
    if s in ("parser", "parser.name", "api"):
        return "parser"
    return "realistic"


def generate_local_part(
    style: str | None = None,
    crypto_len: int = 16,
) -> tuple[str, PersonName | None]:
    st = resolve_style(style)
    if st == "crypto":
        return crypto_local_part(crypto_len), None
    if st == "parser":
        got = _parser_take_one()
        if got:
            return got
        local, p = realistic_local_part()
        return local, p
    local, p = realistic_local_part()
    return local, p


def parser_status() -> dict[str, Any]:
    return {
        "api_key_set": bool(_parser_api_key()),
        "cache_size": len(_PARSER_CACHE),
        "last_error": _PARSER_LAST_ERR or None,
        "country": _parser_country(),
        "corpus": corpus_stats(),
    }


def random_display_name() -> tuple[str, str]:
    p = pick_local_person()
    return p.first, p.last


def reload_corpora() -> dict[str, Any]:
    global _CORPUS, FIRST_NAMES, LAST_NAMES
    _CORPUS = _load_corpora()
    FIRST_NAMES = list(_CORPUS["first_all"])
    LAST_NAMES = list(_CORPUS["last_all"])
    return corpus_stats()
