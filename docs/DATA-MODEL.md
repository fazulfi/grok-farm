# Data Model — Grok Farm

---

## 1. Farm SQLite — `akun.db`

Path: `~/grok-farm/akun.db`

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
| `status` | TEXT | `farmed` \| `injected` \| `error` |
| `injected_at` | TIMESTAMP | When marked injected |
| `grok_cli_connection_id` | TEXT | Optional 9router id |
| `ninerouter_name` | TEXT | Optional display name |
| `notes` | TEXT | Freeform |

### Indexes

- `idx_status` on `status`
- `idx_email` on `email`

### State machine

```
(missing) --import--> farmed --workflow success--> injected
                \-- manual mark --> error
```

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
| GROK_IMAP_* | yes | OTP inbox |
| GROK_EMAIL_MODE | yes | `domain` / `plus_trick` |
| GROK_EMAIL_DOMAIN | if domain | catch-all domain |
| GROK_PASSWORD | yes | account password |
| GROK_PROXY_FILE | prod | path to synced proxy list |
| GROK_CONCURRENT | ops | browsers |
| GROK_MAX_ACCOUNTS | ops | batch size default |
| GROK_HEADLESS | ops | `true` on VPS |

---

## 5. Logging artifacts

| Path | Content |
|------|---------|
| `farm_brutal.log` | systemd / loop stdout (if configured) |
| `workflow.log` | import + inject |
| `batch_*/farm.log` | per-batch farmer detail |
| `screenshots/` | failure diagnostics |

---

## 6. PII & retention

- Emails, tokens, passwords = **credentials**
- Do not commit `akun.db`, `.env`, `results/**`
- Production backup encryption recommended (age/sops/gpg)
