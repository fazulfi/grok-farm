#!/usr/bin/env python3
"""scan_tokens.py — scan akun Grok/xAI, decode JWT, deteksi flag bot (plenger).

Intel 2026-08-07 (grup farmer):
  - Payload JWT dengan `"bfs":1` atau `"bot_flag_source":1` = akun ke-flag → thinking
    TIDAK jalan (plenger). Token sehat TIDAK punya field ini. Buang akun ini.
  - Token sehat biasanya punya scope `... conversations:read conversations:write`
    (cocokkan ke grok-build latest) dan referrer (grok-build / cli-proxy-api).
  - Domain ter-flag massal → ganti domain. Domain baru = aman.

Cara pakai:
  python3 scan_tokens.py results/accounts.txt
  python3 scan_tokens.py --jwt eyJhbG...            # satu token
  python3 scan_tokens.py --jwt-file hasil_extract.txt   # tiap baris satu JWT
  python3 scan_tokens.py results/accounts.txt --json    # output JSON

Format accounts.txt (dari farm.py): email|password|access_token|refresh_token|expires_at
Jika kolom token tidak di posisi ke-3, pakai --col N (0-indexed).

Exit code: 0 = semua sehat | 2 = ada akun ter-flag (buang).
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from pathlib import Path

# Field flag yang kami tandai (evolve dari waktu ke waktu)
FLAG_FIELDS = ("bfs", "bot_flag_source")

# Scope & referrer yang diharapkan untuk akun "thinking jalan" (grok-build latest)
HEALTHY_SCOPE_MARKERS = ("conversations:read", "conversations:write")
HEALTHY_REFERRERS = ("grok-build", "cli-proxy-api")


def _b64url_decode(s: str) -> bytes:
    """Base64url (JWT) decode. Tambah padding bila perlu."""
    pad = "=" * ((4 - len(s) % 4) % 4)
    return base64.urlsafe_b64decode(s + pad)


def decode_jwt_payload(token: str) -> tuple[dict | None, str | None]:
    """Decode JWT payload. Return (payload_dict, error_str)."""
    token = (token or "").strip()
    if not token:
        return None, "empty"
    if "." not in token:
        return None, "not-jwt"
    try:
        header, payload, *_ = token.split(".")
        data = json.loads(_b64url_decode(payload))
        return (data if isinstance(data, dict) else None), None
    except Exception as e:
        return None, f"decode-error: {e}"


def inspect_account(identifier: str, token: str) -> dict:
    payload, err = decode_jwt_payload(token)
    if err or payload is None:
        return {"id": identifier, "error": err or "no-payload", "flagged": None}
    flagged_fields = [f for f in FLAG_FIELDS if payload.get(f) == 1]
    is_flagged = bool(flagged_fields)
    scope = payload.get("scope", "")
    referrer = payload.get("referrer", "")
    bad_flag = bool(payload.get("bot_flag_source")) or bool(payload.get("bfs"))
    return {
        "id": identifier,
        "flagged": is_flagged or bool(flagged_fields),
        "flag_fields": flagged_fields,
        "bfs": payload.get("bfs"),
        "bot_flag_source": payload.get("bot_flag_source"),
        "scope": scope,
        "referrer": referrer,
        "has_conversations": all(m in scope for m in HEALTHY_SCOPE_MARKERS),
        "exp": payload.get("exp"),
        "issued_at": payload.get("iat"),
        # peringatan sekunder (non-blocking)
        "warn_no_conversations": not all(m in scope for m in HEALTHY_SCOPE_MARKERS),
        "warn_referrer": bool(referrer) and referrer not in HEALTHY_REFERRERS,
    }


def load_tokens_from_accounts_file(path: Path, col: int = 2) -> list[tuple[str, str]]:
    out = []
    for line in path.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "|" not in line:
            # coba JSON line: {"email":...,"access_token":...}
            if line.startswith("{"):
                try:
                    o = json.loads(line)
                    em = o.get("email") or o.get("id") or "?"
                    tok = (
                        o.get("access_token")
                        or o.get("access")
                        or o.get("token")
                        or o.get("jwt")
                        or ""
                    )
                    if tok:
                        out.append((em, tok))
                    continue
                except Exception:
                    pass
            continue
        parts = line.split("|")
        if len(parts) <= col:
            continue
        email = parts[0].strip() or f"line{len(out)+1}"
        token = parts[col].strip()
        if token:
            out.append((email, token))
    return out


def load_jwt_lines(path: Path) -> list[tuple[str, str]]:
    out = []
    for i, line in enumerate(path.read_text(errors="replace").splitlines()):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # mungkin email|token
        if "|" in line:
            first, rest = line.split("|", 1)
            tok = rest.split("|")[0].strip()
            if tok.count(".") >= 2:
                out.append((first.strip() or f"line{i+1}", tok))
                continue
        if line.count(".") >= 2:
            out.append((f"line{i+1}", line))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Scan Grok JWT tokens utk deteksi flag bot (plenger)")
    ap.add_argument("path", nargs="?", help="file accounts.txt (email|pw|access|refresh|expires)")
    ap.add_argument("--jwt", help="decode satu JWT langsung")
    ap.add_argument("--jwt-file", help="file berisi satu JWT tiap baris")
    ap.add_argument("--col", type=int, default=2, help="index kolom access token (default 2)")
    ap.add_argument("--json", action="store_true", help="output JSON")
    args = ap.parse_args()

    entries: list[tuple[str, str]] = []
    if args.jwt:
        entries.append(("jwt", args.jwt))
    elif args.jwt_file:
        entries.extend(load_jwt_lines(Path(args.jwt_file)))
    elif args.path:
        p = Path(args.path)
        if not p.is_file():
            print(f"FAIL: file {args.path} gak ketemu"); return 1
        entries.extend(load_tokens_from_accounts_file(p, col=args.col))
    else:
        print("usage: scan_tokens.py <accounts.txt> | --jwt <token> | --jwt-file <f> | --col N")
        return 1

    if not entries:
        print("Tidak ada token yang bisa didecode")
        return 1

    results = [inspect_account(i, t) for i, t in entries]
    flagged = [r for r in results if r.get("flagged")]
    clean = [r for r in results if not r.get("flagged")]

    if args.json:
        print(json.dumps(
            {"total": len(results), "flagged": len(flagged), "clean": len(clean), "results": results},
            indent=2,
        ))
    else:
        # Ringkas per-akun
        for r in results:
            if r.get("error"):
                print(f"  [ERR]  {r['id']}: {r['error']}")
                continue
            mark = "FLAG" if r.get("flagged") else "ok"
            flag_desc = f" ({','.join(r['flag_fields'])})" if r.get("flagged") else ""
            conv = "conv+" if r.get("has_conversations") else "NO-conv"
            ref = f"ref={r.get('referrer') or '(none)'}"
            warns = []
            if r.get("warn_no_conversations"): warns.append("no-conv")
            if r.get("warn_referrer"): warns.append(f"ref={r.get('referrer')}")
            ext = f"  [{' '.join(warns)}]" if warns else ""
            print(f"  [{mark:<4}] {r['id']}  {conv}  {ref}{flag_desc}{ext}")
        # Rekap
        from collections import Counter
        scopes = Counter((r.get("scope") or "NONE") for r in clean)
        refs = Counter((r.get("referrer") or "(none)") for r in clean)
        print()
        print(f"TOTAL   : {len(results)}")
        print(f"FLAGGED : {len(flagged)}  <- buang (kondisi `bfs`/`bot_flag_source`)")
        print(f"CLEAN   : {len(clean)}")
        if clean:
            print("\nScope CLEAN (distribusi):")
            for s, c in scopes.most_common(): print(f"  {c}x  {s}")
            print("Referrer CLEAN:")
            for r_, c in refs.most_common(): print(f"  {c}x  {r_}")
            bad_conv = sum(1 for r in clean if not r.get("has_conversations"))
            if bad_conv:
                print(f"\nWARN: {bad_conv} akun TANPA `conversations:*` di scope — cek scope farm (samain ke grok-build latest).")
        if flagged:
            print("\nAKSI: buang akun ter-flag. Kalau SEMUA ke-flag → ganti domain farm (domain baru = aman).")

    return 2 if flagged else 0


if __name__ == "__main__":
    sys.exit(main())
