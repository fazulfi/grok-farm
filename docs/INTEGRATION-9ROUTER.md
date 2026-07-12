# Integration Contract — 9router

**Version:** 2.0.0  
**Transport:** SSH + local SQLite on gateway (inject) · HTTP optional for health

---

## 1. Purpose

Bridge farmed Grok OAuth tokens into 9router so clients can call models (including geo-restricted **Grok 4.5**) through non-EU egress proxies.

---

## 2. Endpoints & hosts

| Item | Value (example) |
|------|-----------------|
| Gateway SSH | `root@49.12.82.34 -p 39999` |
| Gateway HTTP | `http://127.0.0.1:20128` (loopback on gateway) |
| Health | `GET /api/health` → `{"ok":true}` |
| CLI auth header | `x-9r-cli-token: <16-char machine token>` |
| SQLite | `/var/lib/9router/db/data.sqlite` |

CLI token derivation (9router): SHA256 of machine-id + salt `9r-cli-auth` + cli-secret, first 16 hex chars. Stored under `/var/lib/9router/`.

---

## 3. Proxy pools (source of truth)

### Schema (`proxyPools`)

```sql
CREATE TABLE proxyPools (
  id TEXT PRIMARY KEY,
  isActive INTEGER DEFAULT 1,
  testStatus TEXT,
  data TEXT NOT NULL,  -- JSON blob
  createdAt TEXT NOT NULL,
  updatedAt TEXT NOT NULL
);
```

### JSON `data` shape

```json
{
  "name": "cjfaxfbs-91.123.10.196",
  "proxyUrl": "http://user:pass@host:port/",
  "noProxy": "",
  "type": "http",
  "strictProxy": false,
  "lastTestedAt": null,
  "lastError": null
}
```

### Farm sync

- Script: `sync_proxies_from_9r.py` (farm host)
- Remote helper: `/root/list_proxies.py` prints one `proxyUrl` per line
- Output: `usa_proxies.txt` as `host:port:user:pass` for `farm.py`

### Recommended pool content

- Static residential non-EU IPs (e.g. WebShare cjfaxfbs list)
- Optional rotating endpoint:  
  `http://cjfaxfbs-BR-CA-MX-SG-US-rotate:PASS@p.webshare.io:80`

---

## 4. Provider connections

### 4.1 `grok-cli` (panel: **Grok CLI / Grok Build**)

| Field | Value |
|-------|--------|
| `provider` | `grok-cli` |
| `authType` | `oauth` |
| `name` / `email` | account email |
| `data` | JSON with accessToken, refreshToken, providerSpecificData.proxy* |

Minimal behavioral requirements for Grok 4.5:

- Valid OAuth access token with scopes including `grok-cli:access`
- `connectionProxyEnabled: true`
- `connectionProxyUrl` non-EU

### 4.2 `xai` (panel: **xAI API**)

| Field | Value |
|-------|--------|
| `provider` | `xai` |
| `authType` | `apikey` |
| `name` | `Grok <email>` |
| `data.apiKey` | same JWT access token (or xAI API key if paid) |

Note: free OAuth JWT on `api.x.ai` may hit spending-limit; **Grok CLI path** is the production free-tier path for Build/CLI models.

---

## 5. Inject protocol (production)

### 5.1 Producer (farm)

`workflow.py`:

1. Load all `accounts` where `status='farmed'`
2. `fetch_all_proxies_from_9router()` via `list_proxies.py`
3. Assign **random proxy per account**
4. Stream JSONL over SSH stdin to gateway injector

### 5.2 JSONL line schema

```json
{
  "email": "user@domain.com",
  "access_token": "eyJ...",
  "refresh_token": "...",
  "proxy": "http://user:pass@host:port"
}
```

### 5.3 Consumer (gateway)

`/root/grok_cli_bulk_inject.py`:

- Upsert `grok-cli` row by `name=email`
- Upsert `xai` row by `name=Grok {email}`
- Print `OK <email>` or `FAIL <email>: ...`
- Footer: `SUMMARY <ok> ok <fail> fail`

### 5.4 Idempotency

Re-running inject **updates** existing connection `data` (token refresh + proxy reassignment). Safe to retry.

---

## 6. HTTP API alternative (partial)

`POST /api/providers` with body:

```json
{
  "provider": "xai",
  "apiKey": "<jwt>",
  "name": "Grok user@domain",
  "connectionProxyEnabled": true,
  "connectionProxyUrl": "http://..."
}
```

Requires valid `x-9r-cli-token` when called remotely.  
**Does not** create `grok-cli` OAuth panel entries — use SQLite inject for that.

---

## 7. Geo / model constraints

| Model | EU VPS direct | Via non-EU proxy |
|-------|---------------|------------------|
| `grok-4.5` / variants | Often `permission-denied` region | Works if proxy + token OK |
| Older grok-3 / 4.x | Often OK | OK |

Gateway host in DE/EU **must not** egress Grok 4.5 without proxy.

---

## 8. Acceptance tests

```bash
# From farm user
python3 sync_proxies_from_9r.py    # count > 0
python3 workflow.py                # SUMMARY N ok 0 fail (if pending)

# On gateway
test $(sqlite3 ... "SELECT COUNT(*) FROM proxyPools;") -ge 1
curl -s localhost:20128/api/health | grep ok
```

Dashboard: **Providers → Grok CLI (Grok Build)** shows N connections with proxy set.

---

## 9. Versioning

| Farm version | Injector | Notes |
|--------------|----------|-------|
| 1.x | manual / xai only | Legacy |
| 2.0 | bulk JSONL + grok-cli | Current production |
