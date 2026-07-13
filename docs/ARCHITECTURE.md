# Architecture — Grok Farm Enterprise

**Version:** 2.0.0  
**Status:** Production  
**Last updated:** 2026-07-12

---

## 1. System context

Grok Farm is a **production account-farming pipeline** for xAI Grok CLI OAuth credentials, with optional **auto-injection** into a self-hosted **9router** gateway.

```
┌─────────────┐     catch-all      ┌──────────────┐
│ Cloudflare  │ ─────────────────► │ Gmail IMAP   │
│ Email Route │   *@domain → gmail │ (OTP source) │
└─────────────┘                    └──────▲───────┘
                                          │
┌─────────────┐    residential     ┌──────┴───────┐     SSH/JSONL    ┌─────────────────┐
│ WebShare    │ ─────────────────► │  Farm VPS    │ ───────────────► │ 9router VPS     │
│ Proxy Pools │   (non-EU IPs)     │  (CSA)       │                  │ (ninerouter)    │
└─────────────┘                    │              │ ◄── sync pools ──│ proxyPools SQLite│
                                   │ systemd      │                  │ grok-cli + xai   │
                                   │ farmer loop  │                  │ connections      │
                                   └──────────────┘                  └─────────────────┘
```

---

## 2. Runtime topology

| Role | Host (example) | User | Port / path |
|------|----------------|------|-------------|
| Farmer | CSA VPS `152.42.242.192` | `magadirxwin` (non-root) | `~/grok-farm` |
| Gateway | 9router `49.12.82.34:39999` | `root` SSH | HTTP `20128` |
| OTP inbox | Gmail (1..N) | App password | IMAP `993` |
| Domain pool | e.g. `budgezen.com`, `mypapyr.com` | Cloudflare Email Routing | catch-all → IMAP |

**Hard constraint:** Camoufox must **not** run as root (XPCOM incomplete under root cache).

---

## 3. Component map

### 3.1 Core farmer (`farm.py`)

- Generates unique emails (`domain` multi-pool or `plus_trick`) via `email_identity.py`
- Launches Camoufox (Playwright) with optional proxy
- Signup → OTP (IMAP, resolved per domain) → profile → Turnstile → OAuth PKCE
- Writes per-batch `accounts.txt` / `accounts.json` / `farm.log`

### 3.1b Identity plane (`email_identity.py`)

```
GROK_EMAIL_DOMAINS=budgezen.com,mypapyr.com
        │
        ▼
  IdentityPool.pick_domain()  ── random | round_robin
        │
        ▼
  local@domain  ──►  imap_for_email()  ──►  IMAP OTP poll
```

| Config | Role |
|--------|------|
| `GROK_EMAIL_DOMAINS` | Comma list of catch-all domains (preferred) |
| `GROK_EMAIL_DOMAIN` | Legacy single domain (merged into pool) |
| `GROK_EMAIL_DOMAIN_STRATEGY` | `random` (default) or `round_robin` |
| `GROK_EMAIL_LOCAL_STYLE` | `realistic` (default human names) · `crypto` (hash) · `parser` (API) |
| `GROK_PARSER_NAME_API_KEY` | Optional [parser.name](https://parser.name) generate endpoint |
| `GROK_IMAP_*` | Default inbox shared by all domains |
| `GROK_IDENTITY_FILE` / `identities.json` | Optional multi-IMAP: domain → Gmail map |

**Local-part generation** (`name_gen.py`):
- **realistic** (default): offline first/last corpus → `john.smith`, `j.miller82`, `mary_jones` …
- **crypto**: legacy alnum secrets
- **parser**: `https://api.parser.name/?endpoint=generate` when key set; falls back to realistic on rate-limit/error (free ~100 req/day — not enough alone for unlimited farm)

**Pattern A (recommended start):** multi domain → one Gmail.  
**Pattern B:** multi domain + multi Gmail pairs via `identities.json`.

### 3.2 Orchestration (`brutal_farmer.sh` + systemd)

Unlimited loop:

1. **Sync proxies** from 9router `proxyPools` → `usa_proxies.txt`
2. **Farm** batch (`GROK_MAX` / concurrent from script + `.env`)
3. **Import** batch into SQLite `akun.db`
4. **Workflow inject** all `status=farmed` into 9router
5. Sleep → repeat

### 3.3 Data plane (`akun.db`)

SQLite inventory of farmed accounts and injection state.

### 3.4 Integration plane (9router)

- **proxyPools** — source of truth for residential proxies
- **providerConnections** — `grok-cli` (OAuth-shaped tokens) + `xai` (API key field)
- Injector: `/root/grok_cli_bulk_inject.py` (JSONL over SSH)

---

## 4. Data flow (happy path)

```
email generate
    → browser signup (proxy IP non-EU)
    → OTP email → IMAP poll
    → profile + password + Turnstile
    → OAuth PKCE → access_token + refresh_token
    → results/batch_<id>/accounts.txt
    → import_db.py → akun.db (status=farmed)
    → workflow.py
         → list_proxies.py @ 9router (random proxy per account)
         → bulk inject grok-cli + xai
         → akun.db status=injected
    → client uses 9router → Grok 4.5 via US/non-EU egress
```

---

## 5. Trust & security boundaries

| Boundary | Rule |
|----------|------|
| Secrets | `.env` never committed; Gmail App Password only |
| Tokens | JWT OAuth in `akun.db` + 9router DB — file perms `600` recommended |
| SSH | Farm user key → 9router root for inject/sync only |
| Browser | Non-root user home `~/.cache/camoufox` |
| Region | Grok 4.5 blocked in EU egress — proxies must be non-EU |

---

## 6. Failure domains

| Domain | Failure mode | Mitigation |
|--------|--------------|------------|
| Turnstile | Timeout / unsolved | Residential proxy, concurrent ≤3, headed if possible |
| IMAP | OTP delay | Timeouts in farm.py; retry next account |
| Proxy pool empty | Sync fails | Farmer skips farm round, retries after delay |
| Inject SSH | Network blip | `|| true` in loop; retry next batch |
| OOM | Browser spike | 8G swap + concurrent 3 |
| systemd crash | Process death | `Restart=always` |

---

## 7. Scaling model

| Lever | Effect |
|-------|--------|
| `CONCURRENT` | Parallel browsers (RAM-bound) |
| `ACCOUNTS_PER_BATCH` | Throughput per loop |
| Multi-VPS farmers | Horizontal; serialize inject to 9router |
| Multi-domain pool | Spread domain reputation / burn risk |
| Multi-IMAP identities | OTP capacity + inbox isolation |
| Proxy pool size | IP diversity vs Turnstile/geo |

See [CAPACITY.md](./CAPACITY.md).

---

## 8. Related docs

- [OPERATIONS.md](./OPERATIONS.md) — day-2 ops
- [INTEGRATION-9ROUTER.md](./INTEGRATION-9ROUTER.md) — inject contract
- [DATA-MODEL.md](./DATA-MODEL.md) — schemas
- [RUNBOOK.md](./RUNBOOK.md) — incidents
