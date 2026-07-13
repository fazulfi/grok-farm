# Data Model — Grok Farm

---

## 1. Farm SQLite — `akun.db`

Path: `~/grok-farm/akun.db` (override: `GROK_AKUN_DB`)  
Permissions: owner farmer user, mode **600** (`db_schema.harden_db_file`).

Schema migrations are additive via `db_schema.migrate()` (import/workflow/health).

### Table `accounts`

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER PK | Auto increment |
| `email` | TEXT UNIQUE | xAI login email |
| `password` | TEXT | Shared/farm password |
| `access_token` | TEXT | OAuth JWT access |
| `refresh_token` | TEXT | OAuth refresh |
| `created_at` | TIMESTAMP | Insert time |
| `batch_id` | TEXT | e.g. `batch_20260712_183504_201d0f` |
| `proxy_used` | TEXT | Proxy URL at inject time |
| `status` | TEXT | Pipeline mark only: `farmed` \| `injected` \| `error` (not session lifecycle) |
| `injected_at` | TIMESTAMP | When marked injected |
| `grok_cli_connection_id` | TEXT | Optional 9router id |
| `ninerouter_name` | TEXT | Optional display name |
| `notes` | TEXT | Freeform (e.g. `token_expired`) |
| `token_exp` | INTEGER | JWT `exp` unix seconds (nullable) |
| `token_health` | TEXT | `ok` \| `expiring_soon` \| `expired` \| `invalid` \| `missing` |
| `last_probe_at` | TEXT | ISO UTC of last live probe (nullable) |
| `last_probe_status` | TEXT | Live probe result (see probe statuses) |
| `last_probe_http` | INTEGER | HTTP status from last probe (nullable) |
| `needs_relogin` | INTEGER | Soft inventory flag: `1` if last live probe rejected the token; `0` if last probe `alive`; unchanged on transient. **Does not** trigger re-auth or session recovery on the farm |

Probe columns are additive via `token_util.ensure_probe_columns` (called from `db_schema.migrate`).

### Live probe statuses (`last_probe_status`)

| Status | Meaning |
|--------|---------|
| `alive` | API accepted Bearer (HTTP 2xx) |
| `needs_relogin` | HTTP 401/403 — token rejected (soft inventory meta only; optional hard mark; **not** a re-auth workflow) |
| `rate_limited` | HTTP 429 |
| `spend_limited` | Spending / quota style body or 402-class |
| `network_error` | Transport / timeout |
| `invalid` | Malformed token or unexpected response |
| `missing` | Empty access token |
| `jwt_expired` | Offline JWT `exp` passed (skip live HTTP when `GROK_PROBE_SKIP_EXPIRED=1`) |

**Soft default:** probe updates meta only. Never set `status=error` on `injected` unless operator passes `--mark-error` (CLI) — preserves 9router connection IDs. Probe/JWT columns are **observability**, not session management; farm product scope is autofarm + auto-inject only.

### Table `proxy_stats`

Tracks inject outcomes per proxy host (credentials stripped from key).

| Column | Type | Description |
|--------|------|-------------|
| `proxy_key` | TEXT PK | Scheme + host:port (no user:pass) |
| `success_count` | INTEGER | Successful injects |
| `fail_count` | INTEGER | Failed injects |
| `last_success_at` | TIMESTAMP | Last success |
| `last_fail_at` | TIMESTAMP | Last fail |
| `last_email` | TEXT | Last related account email |
| `score` | REAL | `success / (success+fail)` in \[0,1\] |
| `updated_at` | TIMESTAMP | Last update |

### Table `domain_stats`

Tracks farm/OTP outcomes per catch-all domain so multi-domain pools can auto-skip dead routing.

| Column | Type | Description |
|--------|------|-------------|
| `domain` | TEXT PK | Lowercase domain (no `@`) |
| `success_count` | INTEGER | Successful farm completions |
| `fail_count` | INTEGER | Failed farm attempts (incl. OTP timeout) |
| `consecutive_fails` | INTEGER | Reset to 0 on success; used for auto-skip |
| `last_success_at` | TIMESTAMP | Last success |
| `last_fail_at` | TIMESTAMP | Last fail |
| `last_email` | TEXT | Last related account email |
| `score` | REAL | `success / (success+fail)` in \[0,1\] |
| `disabled` | INTEGER | Manual disable flag (0/1); success clears auto path |
| `updated_at` | TIMESTAMP | Last update |

**Auto-skip rule:** domain is skipped by `IDENTITY_POOL.pick_domain` when `disabled=1` **or** `consecutive_fails >= GROK_DOMAIN_MAX_CONSECUTIVE_FAILS` (default **3**). If all pool domains would be skipped, pick fails open to the full pool so farming does not hard-stop.

### Indexes

- `idx_status` on `accounts(status)`
- `idx_email` on `accounts(email)`
- `idx_proxy_score` on `proxy_stats(score DESC)`
- `idx_domain_score` on `domain_stats(score DESC)`

### Pipeline status flow (inject queue — not session lifecycle)

```
(missing) --import--> farmed --workflow success--> injected
                 \-- dead JWT on import / inject fail / mark_expired --> error
```

- **`farmed`:** credentials in DB, waiting for on-path inject (steady state ≈ 0 when farmer is healthy).
- **`injected`:** handoff to 9router succeeded; farm no longer owns session validity.
- **`error`:** pipeline failure or dead farmed JWT — recovery is **re-farm + inject**, not reauth.
- Soft columns (`token_health`, `needs_relogin`, `last_probe_*`) do **not** move rows through a session state machine.

### Import fail-closed (JWT)

On `import_db.py` / `workflow.import_batches`:

| Access token health | Insert `status` | `notes` |
|---------------------|-----------------|---------|
| `ok` / `expiring_soon` | `farmed` | null |
| `expired` | `error` | `token_expired` |
| `invalid` / missing / non-JWT | `error` | `bad_token` |

Existing rows: if already `farmed` and re-import sees dead JWT, row is upgraded to `error` (does not reopen `injected`).

### Mark expired batch

`token_util.mark_expired_accounts` / `mark_expired_tokens.py`:

- Scans `status IN ('farmed','injected')`
- Always refreshes `token_exp` + `token_health` (unless `--dry-run`)
- Marks **farmed** dead → `error`
- Marks **injected** dead → `error` only with `include_injected=True` / `--include-injected`

---

## 2. Batch files

### `results/batch_<id>/accounts.txt`

Pipe-delimited, one account per line:

```
email|password|access_token|refresh_token|iso_timestamp
```

### `results/batch_<id>/accounts.json`

Array of objects (farm.py native export) with token fields and metadata.

### `results/used_emails.txt`

Dedup ledger — emails already attempted/used across runs.

---

## 3. 9router SQLite — selected tables

Path: `/var/lib/9router/db/data.sqlite`

### `proxyPools`

| Column | Type |
|--------|------|
| id | TEXT PK |
| isActive | INTEGER |
| testStatus | TEXT |
| data | TEXT (JSON) |
| createdAt / updatedAt | TEXT |

### `providerConnections`

| Column | Type |
|--------|------|
| id | TEXT PK |
| provider | TEXT (`grok-cli`, `xai`, …) |
| authType | TEXT (`oauth`, `apikey`, …) |
| name | TEXT |
| email | TEXT |
| priority | INTEGER |
| isActive | INTEGER |
| data | TEXT (JSON) |
| createdAt / updatedAt | TEXT |

### `grok-cli` data JSON (inject shape)

```json
{
  "displayName": "Noah White",
  "accessToken": "eyJ...",
  "refreshToken": "...",
  "expiresAt": "2026-07-12T22:00:00",
  "scope": "openid profile email offline_access grok-cli:access ...",
  "testStatus": "active",
  "expiresIn": 21600,
  "providerSpecificData": {
    "authMethod": "api",
    "idToken": "eyJ...",
    "email": "user@domain",
    "userId": "uuid",
    "hasGrokCodeAccess": true,
    "connectionProxyEnabled": true,
    "connectionProxyUrl": "http://user:pass@host:port",
    "connectionNoProxy": ""
  },
  "lastRefreshAt": "...",
  "errorCode": null,
  "backoffLevel": 0
}
```

### `xai` data JSON (inject shape)

```json
{
  "apiKey": "eyJ...",
  "providerSpecificData": {
    "connectionProxyEnabled": true,
    "connectionProxyUrl": "http://user:pass@host:port",
    "connectionNoProxy": ""
  }
}
```

---

## 4. Environment variables (farm)

See `.env.example`. Critical keys:

| Key | Required | Description |
|-----|----------|-------------|
| GROK_IMAP_* | yes (or identities file) | Default OTP inbox |
| GROK_EMAIL_MODE | yes | `domain` / `plus_trick` |
| GROK_EMAIL_DOMAINS | preferred if domain | Comma-separated catch-all domains |
| GROK_EMAIL_DOMAIN | legacy | Single domain; merged into pool |
| GROK_EMAIL_DOMAIN_STRATEGY | ops | `random` \| `round_robin` |
| GROK_IDENTITY_FILE | optional | Path to multi-IMAP JSON map |
| GROK_PASSWORD | yes | account password |
| GROK_PROXY_FILE | prod | path to synced proxy list |
| GROK_CONCURRENT | ops | browsers |
| GROK_MAX_ACCOUNTS | ops | batch size default |
| GROK_HEADLESS | ops | `true` on VPS |
| GROK_AKUN_DB | ops | override path to `akun.db` |
| GROK_FARM_DIR | ops | override farm home for health CLI |
| GROK_ALERT_WEBHOOK | optional | Discord-style webhook for ops alerts |
| GROK_9R_SSH / GROK_9R_PORT / GROK_9R_KEY | ops | 9router SSH inject path |

Backup-only (`~/.config/grok-farm/backup.env`, not in git):

| Key | Description |
|-----|-------------|
| AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY | S3 credentials |
| S3_ENDPOINT / S3_BUCKET / S3_PREFIX | Object storage |
| AGE_RECIPIENT | age public key (`age1...`) |
| BACKUP_ENCRYPT | `age` (default) or `none` (emergency) |

---

## 5. Logging artifacts

| Path | Content |
|------|---------|
| `farm_brutal.log` | systemd / loop stdout (if configured) |
| `workflow.log` | import + inject (redacted secrets) |
| `batch_*/farm.log` | per-batch farmer detail |
| `logs/s3_backup.log` | hourly backup log (health CLI reads age) |
| `screenshots/` | failure diagnostics |

---

## 6. PII & retention

- Emails, tokens, passwords = **credentials**
- Do not commit `akun.db`, `.env`, `results/**`
- Production backup encryption required (`age` → `.tgz.age` on S3)
- Retention default: 14 days (`RETENTION_DAYS` in backup.env)
