# Security Policy — Grok Farm

---

## 1. Threat model (summary)

| Asset | Risk if leaked |
|-------|----------------|
| Gmail App Password | Inbox takeover / OTP theft |
| xAI account password | Account takeover |
| OAuth access/refresh tokens | API/CLI abuse until expiry/revoke |
| Proxy credentials | Bandwidth theft / IP burn |
| 9router CLI token | Unauthorized provider mutations |
| S3 backup credentials | Offsite vault theft |
| age private identity | Decrypt all historical backups |

---

## 2. Hard requirements

1. **Never commit** `.env`, `akun.db`, `results/`, `proxies.txt`, `usa_proxies.txt`, `backup.env`, age identity files
2. **Never run Camoufox/farm as root**
3. Prefer **Gmail App Passwords** over primary password
4. SSH keys for farm→gateway: dedicated key, `chmod 600`, no passphrase in automation or use ssh-agent carefully
5. 9router dashboard not public without auth
6. Offsite backups **must be encrypted** before upload (`age` default)
7. Farmer user must **not** have `NOPASSWD:ALL` — only required systemctl commands if needed
8. `akun.db` and secret files: owner farmer user, mode **600**
9. Logs/webhooks must not ship full JWTs or proxy `user:pass` (use `log_redact.py`)
10. Do not put real VPS passwords, API keys, or age private keys in repo docs

---

## 3. Secrets handling

| Secret | Storage |
|--------|---------|
| `.env` | Server only + encrypted backup |
| `akun.db` | `chmod 600`, owner farmer user |
| Proxy lists | 9router DB; farm file is derived cache |
| S3 keys | `~/.config/grok-farm/backup.env` (`chmod 600`) |
| age public key | `~/.config/grok-farm/age.pubkey` or `AGE_RECIPIENT` in backup.env |
| age private identity | **Operator vault** (see below). Prefer **not** only on farm VPS |
| GitHub | Private repo **without** secrets |

### age identity vault (no USB required)

Farm VPS only needs the **public** recipient to encrypt. The **private** identity decrypts every historical backup — treat it like a root password.

| Option | How | When |
|--------|-----|------|
| **A. Password manager** (recommended) | Store full `age.identity` file contents as a secure note (Bitwarden/1Password/etc.) | No USB; works from any trusted machine |
| **B. Second host / operator laptop** (recommended with A) | Copy identity off the farm host: `chmod 600 ~/.config/grok-farm/age.identity` | Daily restore capability |
| **C. Encrypted local file** | `age -p -o age.identity.age age.identity` (passphrase) on operator disk | Offline-ish without USB |
| **D. Temporary VPS root-only** | Identity at `/root/.config/grok-farm/age.identity` (`chmod 600`, root-only) | **Interim only** until A/B/C done |

**Operator laptop path (Windows / no USB):**

| Item | Path / control |
|------|----------------|
| Private identity | `%USERPROFILE%\.config\grok-farm\age.identity` (e.g. `C:\Users\<you>\.config\grok-farm\age.identity`) |
| Public key (safe) | same dir `age.pubkey` (`age1…`) |
| ACL | remove inheritance; grant FullControl only to your user + `SYSTEM` (`icacls … /inheritance:r /grant:r "%USERNAME%:(F)" "SYSTEM:(F)"`) |
| Decrypt | install [age](https://github.com/FiloSottile/age) for Windows, or decrypt on a Linux host that has the identity |

**Farm hosts:** prefer **pubkey-only** on VPS (`age.pubkey` / `AGE_RECIPIENT`). Private `age.identity` stays on the operator laptop (B) and/or password manager (A). Without an offline private copy, S3 restore is impossible if the VPS is lost.

Do **not** commit `age.identity` or paste it into chat/tickets.

### Backup encryption (required for enterprise)

S3 hourly backup (`scripts/s3_backup.sh`) defaults to `BACKUP_ENCRYPT=age`:

```bash
# One-time on operator machine (or generate on VPS then vault immediately)
age-keygen -o ~/.config/grok-farm/age.identity
# public line: age1...
grep 'public key:' ~/.config/grok-farm/age.identity | awk '{print $NF}' \
  > ~/.config/grok-farm/age.pubkey

# On farm VPS — public key only (preferred long-term)
mkdir -p ~/.config/grok-farm
# write age1... to ~/.config/grok-farm/age.pubkey
# or: AGE_RECIPIENT=age1... in backup.env
chmod 600 ~/.config/grok-farm/backup.env
# install: sudo apt-get install -y age

# Manual encrypt single file
age -r age1... -o akun.db.age akun.db

# Decrypt restore (operator only — needs private identity from vault)
age -d -i ~/.config/grok-farm/age.identity -o restore.tgz backup.tgz.age
./scripts/restore_farm.sh magadirxwin@FARM_IP ./backup.tgz.age
```

`BACKUP_ENCRYPT=none` is emergency-only and is a security defect for production.

**S3 object policy:** keep `*.tgz.age` + `LATEST.txt`. Delete historical plaintext `*.tgz` after encryption is verified.

### Log redaction

- Module: `log_redact.py` (`redact_text`, `redact_proxy_url`, `safe_print`)
- Wired into `workflow.py` logs and `alerts.py` webhook bodies
- Never POST full JWT to Discord/Telegram

### Alerts

Optional channels (either or both). Wired in:

- `alerts.py` + `workflow.py` (empty proxy pool, inject all-fail / partial / error)
- **`scripts/health_check.sh`** via `grok-farm-health.timer` — inventory
  `check_status` **hard** exit **3** / unexpected failure only (soft exit **2** is
  log-only). Debounced via `GROK_ALERT_DEBOUNCE_MIN` (default 60 min;
  `logs/alert_debounce/`).
- **`daily_digest.py`** via `grok-farm-digest.timer` — daily health + proxy
  dashboard HTML card (`skip_debounce`); proxy keys redacted; **no** JWT/password.
  Fleet leader uses sticky `editMessageText` (state file mode **600** under
  `~/.config/grok-farm/telegram_sticky_*.json`).

| Channel | Env | Notes |
|---------|-----|-------|
| Discord-style webhook | `GROK_ALERT_WEBHOOK` / `GROK_FARM_ALERT_WEBHOOK` | JSON `{"content":...}` |
| Telegram Bot API | `GROK_TELEGRAM_BOT_TOKEN` + `GROK_TELEGRAM_CHAT_ID` | `sendMessage` / sticky `editMessageText`; **never** log/commit token |

**Do not invent a fake webhook** — leave unset if unused.

```bash
# .env (optional)
# GROK_ALERT_WEBHOOK=https://discord.com/api/webhooks/...
# GROK_FARM_ALERT_WEBHOOK=...
# GROK_TELEGRAM_BOT_TOKEN=   # secret — chmod 600 .env
# GROK_TELEGRAM_CHAT_ID=
# GROK_ALERT_DEBOUNCE_MIN=60
# GROK_HOST_TAG=grok4   # optional digest title label
# GROK_FLEET_DIGEST_STICKY=1
```

Bodies pass through `log_redact` (no full JWT / proxy user:pass / bot token). Health timer posts only hard issue codes + account counts (no tokens). Digest posts redacted proxy keys + account counts only. Sticky state stores `message_id` + `chat_id` only (no tokens).

### Windows age CLI (operator laptop)

| Method | Command |
|--------|---------|
| Scoop | `scoop install age` |
| Chocolatey | `choco install age.portable` |
| Manual | [FiloSottile/age releases](https://github.com/FiloSottile/age/releases) → add to PATH |

Decrypt: `age -d -i %USERPROFILE%\.config\grok-farm\age.identity -o restore.tgz backup.tgz.age`

If CLI not installed, decrypt on any Linux host that has the vaulted identity (VPS root-only interim path is last resort).

### Multi-Gmail secrets (`identities.json`)

- Store only on VPS: `~/grok-farm/identities.json` owner farmer, mode **600**
- **Never** commit real App Passwords; use `identities.example.json` placeholders only
- If an App Password appeared in chat/logs: **rotate** in Google Account → App passwords after rollout
- See RUNBOOK **R11** for Cloudflare destination + catch-all per domain

---

## 4. Network

- Outbound HTTPS to `accounts.x.ai`, `auth.x.ai`, `api.x.ai`, `grok.com`, IMAP
- Farm→Gateway: SSH only (port custom)
- Gateway HTTP API: prefer loopback + CLI token; do not expose `20128` publicly without reverse proxy auth
- S3: HTTPS to configured endpoint (e.g. `https://is3.cloudhost.id`)

---

## 5. Least privilege (SSH / sudo)

| Control | Target |
|---------|--------|
| Farmer user | non-root; no `NOPASSWD:ALL` |
| systemd control | if needed: `systemctl start/stop/restart/status grok-farmer` only via limited sudoers |
| Farm→9router key | dedicated key; restrict `authorized_keys` from= / command= when possible |
| Key files | `chmod 600` |

Example limited sudoers drop-in (prefer over ALL):

```
magadirxwin ALL=(root) NOPASSWD: /bin/systemctl start grok-farmer, /bin/systemctl stop grok-farmer, /bin/systemctl restart grok-farmer, /bin/systemctl status grok-farmer, /bin/systemctl is-active grok-farmer
```

---

## 6. Rotation procedures

| Event | Action |
|-------|--------|
| `.env` leaked | Rotate Gmail app password + GROK_PASSWORD; re-farm critical accounts |
| GitHub secret scan alert | Purge history / rotate all credentials |
| Proxy vendor compromise | Wipe proxyPools; re-import |
| Staff offboarding | Revoke SSH keys; rotate tokens; rotate age identity if shared |
| S3 keys leaked | Rotate S3 keys in backup.env; re-encrypt strategy if needed |
| age identity leaked | Generate new age keypair; re-backup; treat old archives as compromised |

---

## 7. Logging hygiene

- Do not ship full JWT in external log aggregators or webhooks
- `farm.log` / `workflow.log` may contain emails — treat as sensitive
- Proxy URLs logged only after `redact_proxy_url` (credentials stripped)
- Screenshots may show PII — restrict access
- Token health labels (`ok` / `expiring_soon` / `expired`) are safe to log; raw tokens are not

---

## 8. Token health & expired JWT policy

- `token_util.py` decodes JWT `exp` without signature verify
- Columns: `accounts.token_exp`, `accounts.token_health`
- Values: `ok` \| `expiring_soon` \| `expired` \| `invalid` \| `missing`
- **Import fail-closed:** `import_db.py` / `workflow.import_batches` insert dead JWT as `status=error` (`notes=token_expired|bad_token`), never as injectable `farmed`
- **Inject path:** workflow skips inject for expired/invalid → `status=error`
- **Batch cleanup:** `python3 mark_expired_tokens.py` (farmed-only default) or `python3 check_status.py --mark-expired`
- **Injected inventory (enterprise default = soft):** leave `status=injected`, update JWT meta only (`many_expired_tokens` soft issue). **Do not** cron `--include-injected` — preserves 9router connection IDs / inject marks. Optional hard-mark is inventory hygiene only and **does not** revoke gateway `providerConnections` (see RUNBOOK R10)
- **Soft meta ≠ session manager:** probe/`needs_relogin`/`token_health` on injected rows are observability only. Farm does **not** re-auth or keep sessions alive after inject; post-inject validity is 9router/consumer
---

## 9. Compliance note

Automating account creation may violate third-party ToS. Operators are responsible for legal/compliance review in their jurisdiction. This software is provided for infrastructure automation research and authorized environments only.
