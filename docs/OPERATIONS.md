# Operations Guide — Grok Farm

**Audience:** operators / on-call  
**Environment:** Linux VPS (Ubuntu 24.04+), systemd, non-root farmer user

---

## 1. Service inventory

| Service | Host | Unit / process | Purpose |
|---------|------|----------------|---------|
| `grok-farmer` | Farm VPS | `systemd` (`brutal_farmer.sh` **v5**) | Unlimited farm → import → inject loop |
| Camoufox | Farm VPS | child of `farm.py` | Browser automation |
| 9router | Gateway VPS | `next-server` / custom | LLM gateway + proxyPools |
| SSH tunnel path | Farm → Gateway | OpenSSH key | Proxy sync + inject |
| `grok-farm-backup.timer` | Farm VPS | systemd timer | Hourly age-encrypted backup: **S3** `s3://grok-farm/farm-vps/csa/grok/` when `~/.config/grok-farm/backup.env` present; else local `~/grok-farm/backups/` |

### Auto import / inject (brutal v5)

Pipeline is automatic when farmer runs as the **non-root** user:

1. After each `farm.py` exit → `import_db.py` + `workflow.py` (9router inject).
2. **Mid-batch drain** every `GROK_MID_DRAIN_INTERVAL` seconds (default **120**; set `0` to disable) while farm is still running.
3. `workflow.log` / `farm_brutal.log` must be **writable by farmer user**. If root-owned, you get `Permission denied` and **nothing reaches 9router** even though `accounts.txt` exists.

```bash
# Fix if inject stuck (common after root-run tests)
sudo chown magadirxwin:magadirxwin ~/grok-farm/workflow.log ~/grok-farm/farm_brutal.log
# Verify auto path
grep -E 'SUMMARY|Permission denied|import\+workflow|mid-drain|Brutal Farmer v' ~/grok-farm/farm_brutal.log | tail -30
```

---

## 2. Daily operations

### 2.1 Health checks

```bash
# Farm VPS — preferred single surface
cd ~/grok-farm
python3 check_status.py
python3 check_status.py --json

# Manual
systemctl is-active grok-farmer
systemctl status grok-farmer --no-pager
free -h
swapon --show
tail -50 ~/grok-farm/farm_brutal.log
tail -50 ~/grok-farm/workflow.log
tail -30 ~/grok-farm/logs/s3_backup.log
ls -l ~/grok-farm/akun.db   # expect -rw------- (600)

# Gateway VPS
curl -s http://127.0.0.1:20128/api/health
sqlite3 /var/lib/9router/db/data.sqlite \
  "SELECT COUNT(*) FROM proxyPools;
   SELECT COUNT(*) FROM providerConnections WHERE provider='grok-cli';
   SELECT COUNT(*) FROM providerConnections WHERE provider='xai';"
```

`check_status.py` reports: farmer unit, account counts, token health, **live probe** (`needs_relogin` + last_probe_status), proxy file lines, top `proxy_stats`, domain pool / `domain_stats`, disk, last backup log age, issues list. Exit `2` if unhealthy (includes soft `needs_relogin` / `many_expired_tokens`).

```bash
python3 check_status.py
python3 check_status.py --json
# Live probe sample (soft meta; default injected only)
python3 check_status.py --probe --probe-limit 20
python3 probe_tokens.py --limit 50
python3 probe_tokens.py --dry-run --limit 5

# Mark dead farmed JWTs as status=error (same as mark_expired_tokens.py)
python3 check_status.py --mark-expired
# Optional: also mark injected expired as error (inventory hygiene; no gateway revoke)
python3 check_status.py --mark-expired-injected

python3 mark_expired_tokens.py --dry-run
python3 mark_expired_tokens.py

# Gateway vs DB inventory (no tokens printed)
python3 reconcile_9router.py
python3 reconcile_9router.py --json --limit-print 5

# Adaptive concurrent decision for next farm batch
python3 adaptive_concurrent.py --print
python3 adaptive_concurrent.py --json
```

See RUNBOOK **R10** (expired JWT), **R13** (probe), **R14** (reconcile), **R15** (adaptive concurrent).

### 2.2 Expected steady state

- `grok-farmer`: **active (running)**
- Concurrent Camoufox parents ≈ adaptive `CONCURRENT` (base 3, bounds 1–5 when `GROK_ADAPTIVE_CONCURRENT=1`)
- `workflow_pools` / `usa_proxies.txt` line count ≈ proxyPools count
- `farmed` backlog near **0** after each successful inject cycle
- Swap present if concurrent ≥ 3
- Backup log age &lt; ~2h when timer enabled; objects on S3 end with `.tgz.age`
- `akun.db` mode 600

---

## 3. Lifecycle commands

```bash
# Start / stop / restart farmer
sudo systemctl start grok-farmer
sudo systemctl stop grok-farmer
sudo systemctl restart grok-farmer

# Enable on boot
sudo systemctl enable grok-farmer

# Follow logs
journalctl -u grok-farmer -f
tail -f ~/grok-farm/farm_brutal.log
tail -f ~/grok-farm/workflow.log
```

### Manual one-shot farm (no systemd)

```bash
cd ~/grok-farm
source .venv/bin/activate
python3 sync_proxies_from_9r.py
python farm.py -n 5 -c 2 -y
python3 import_db.py
python3 workflow.py
python3 check_status.py
```

---

## 4. Configuration

### 4.1 Farmer knobs

| File | Keys |
|------|------|
| `~/grok-farm/.env` | IMAP, **domain pool**, password, headless, proxy file, optional `GROK_ALERT_WEBHOOK` |
| `identities.json` | Optional multi-IMAP map (see `identities.example.json`) — **chmod 600**, never git |
| `brutal_farmer.sh` | `ACCOUNTS_PER_BATCH`, `CONCURRENT`, `BATCH_DELAY` |
| `systemd` unit | `User=`, `WorkingDirectory=`, `Restart=` |
| `~/.config/grok-farm/backup.env` | S3 + `AGE_RECIPIENT` / `BACKUP_ENCRYPT` |

### 4.1b Multi-domain workflow (enterprise)

**Goal:** farm across several catch-all domains without code change per domain.

#### Prerequisites (per domain)

1. DNS domain active (Cloudflare recommended)
2. **Email Routing → Catch-all → Action: Forward to Gmail** = **Active**
3. Destination Gmail = `GROK_IMAP_USER` (or mapped inbox in `identities.json`)
4. Gmail App Password in `.env` / identity file

#### Enable pool on live farmer (live-safe)

```bash
# On farm VPS as farmer user
cd ~/grok-farm
# Example: add mypapyr.com beside budgezen.com
grep -q '^GROK_EMAIL_DOMAINS=' .env \
  && sed -i 's|^GROK_EMAIL_DOMAINS=.*|GROK_EMAIL_DOMAINS=budgezen.com,mypapyr.com|' .env \
  || echo 'GROK_EMAIL_DOMAINS=budgezen.com,mypapyr.com' >> .env
# Keep legacy key in sync (optional)
sed -i 's|^GROK_EMAIL_DOMAIN=.*|GROK_EMAIL_DOMAIN=budgezen.com|' .env
# Strategy: round_robin (recommended) = A,B,A,B… across healthy domains
#            random      = weighted random (identity.weight)
grep -q '^GROK_EMAIL_DOMAIN_STRATEGY=' .env \
  && sed -i 's|^GROK_EMAIL_DOMAIN_STRATEGY=.*|GROK_EMAIL_DOMAIN_STRATEGY=round_robin|' .env \
  || echo 'GROK_EMAIL_DOMAIN_STRATEGY=round_robin' >> .env

# OTP smoke test (send to probe@newdomain, confirm in Gmail)
# python3 -c "from email_identity import load_identity_pool; from pathlib import Path; p=load_identity_pool(Path('.')); print(p.summary()); print([p.pick_domain() for _ in range(6)])"

# Next batch of systemd farmer reloads .env via farm.py — no restart required for env-only
# If you changed code modules: next process start picks them up; restart only if needed:
# sudo systemctl restart grok-farmer
```

#### Realistic email names (not hash)

```bash
# Default offline human local-parts (recommended)
grep -q '^GROK_EMAIL_LOCAL_STYLE=' .env \
  && sed -i 's|^GROK_EMAIL_LOCAL_STYLE=.*|GROK_EMAIL_LOCAL_STYLE=realistic|' .env \
  || echo 'GROK_EMAIL_LOCAL_STYLE=realistic' >> .env

# Optional: parser.name API (register free key at https://parser.name/register)
# Free tier ~100 requests/day — use as spice only; farm falls back to offline realistic
# echo 'GROK_PARSER_NAME_API_KEY=your_key' >> .env
# echo 'GROK_PARSER_NAME_COUNTRY=US' >> .env
# GROK_EMAIL_LOCAL_STYLE=parser

# Smoke samples
python3 -c "from name_gen import generate_local_part; print([generate_local_part('realistic')[0] for _ in range(8)])"
```

Examples: `brandon.howard@domain`, `rizky.saputra@domain`, `k.little92@domain`.

**Corpora (drop more files anytime):**

| File | Source |
|------|--------|
| `data/names_id.txt` | Indonesian names — [gist maulvi/nama.txt](https://gist.github.com/maulvi/e443e22b82a1dc24e14344b47f0a80ea) cleaned |
| `data/names_en_first.txt` / `names_en_last.txt` | English |
| `data/names_intl_first.txt` | International given names |
| `data/names_extra_first.txt` / `names_extra_last.txt` | Operator-supplied (one name/line) |

```bash
# Prefer Indonesian-heavy names
grep -q '^GROK_NAME_REGION=' .env \
  && sed -i 's|^GROK_NAME_REGION=.*|GROK_NAME_REGION=id|' .env \
  || echo 'GROK_NAME_REGION=id' >> .env
python3 -c "from name_gen import corpus_stats, generate_local_part; print(corpus_stats()); print([generate_local_part()[0] for _ in range(8)])"
```

#### Domain pick + workflow safety

| Step | Contract |
|------|----------|
| Pick domain | `IDENTITY_POOL.pick_domain(skip_domains=domain_stats)` — round_robin or random |
| Skip dead | consecutive OTP/farm fails ≥ `GROK_DOMAIN_MAX_CONSECUTIVE_FAILS` (default 3) |
| Fail-open | if all domains would skip → still pick from full pool (never hard-stop farm) |
| OTP IMAP | `imap_for_email(addr)` per domain (shared Gmail or `identities.json`) |
| Import | `workflow.import_batches` → `status=farmed` (IntegrityError = already known) |
| Inject | proxies **only** from 9router `proxyPools`; empty pool → abort (no local fallback) |
| Mark DB | commit `status=injected` **before** proxy_stats (scoring errors never undo inject) |
| Bad/expired JWT | mark `status=error` (`bad_token` / `token_expired`) — do not re-queue forever |

#### Verify

```bash
python3 check_status.py
# expect: identity domains=2 pool lists both; strategy=round_robin
# domain_stats: tracks OTP/farm success; auto-skips dead domains after consecutive fails
# After picks: alternate domains in farm.log (round_robin)
tail -f results/batch_*/farm.log | grep -E 'start|wait_otp|OK|FAIL|mypapyr|budgezen'
```

#### Domain health auto-skip

- Table `domain_stats` (see DATA-MODEL): updated on each farm success/fail from `farm.py`.
- After `GROK_DOMAIN_MAX_CONSECUTIVE_FAILS` (default 3) consecutive fails, domain is skipped on pick.
- If **all** pool domains would be skipped → fail-open (still pick from full pool).
- Re-enable after routing fix: RUNBOOK **R3d** SQL or remove domain from pool until IMAP probe green.
- CSA ops note (2026-07-13): mypapyr.com catch-all claimed Active but zero xAI `To:` in IMAP — pool temporarily `budgezen.com` only until R3c green.

#### Multi-Gmail (optional Pattern B)

See RUNBOOK **R11** for full Cloudflare + safe rollout.

```bash
cp identities.example.json identities.json
chmod 600 identities.json
# Edit: primary domains + secondary (enabled only after CF destination verify)
# Pattern B example: primary owns domain-a; secondary owns domain-b after routing green
nano identities.json   # real App Passwords; never commit
grep -q '^GROK_IDENTITY_FILE=' .env \
  || echo 'GROK_IDENTITY_FILE=~/grok-farm/identities.json' >> .env
python3 -c "from email_identity import load_identity_pool; print(load_identity_pool('.').summary())"
# Live-safe reload farm.py process (not full systemd stop unless needed)
pkill -f 'python.*farm\.py' || true
python3 check_status.py   # identity ids >= 2 when secondary enabled
```

**Rules:** never commit `identities.json`; chmod 600; redaction applies to logs; App Password in chat → **rotate** after rollout; one leak → rotate that identity only.

### 4.2 Change concurrent

```bash
# Edit script
sed -i 's/^CONCURRENT=.*/CONCURRENT=3/' ~/grok-farm/brutal_farmer.sh
# Align .env default prompt
sed -i 's/^GROK_CONCURRENT=.*/GROK_CONCURRENT=3/' ~/grok-farm/.env
sudo systemctl restart grok-farmer
```

### 4.3 Proxy source of truth

**Only 9router → Proxy Pools UI/DB.**

- Add/remove pools on 9router
- Next farmer loop runs `sync_proxies_from_9r.py`
- Inject uses live pool list (random per account)
- Inject success/fail recorded in `proxy_stats` (host-only key)

Do **not** hand-edit `usa_proxies.txt` for production; it is overwritten by sync.

---

## 5. Account inventory

```bash
# Summary + token health + proxy scores
python3 ~/grok-farm/check_status.py

# SQL
sqlite3 ~/grok-farm/akun.db \
  "SELECT status, COUNT(*) FROM accounts GROUP BY status;"
sqlite3 ~/grok-farm/akun.db \
  "SELECT token_health, COUNT(*) FROM accounts GROUP BY token_health;"
sqlite3 ~/grok-farm/akun.db \
  "SELECT proxy_key, success_count, fail_count, score FROM proxy_stats ORDER BY score DESC LIMIT 10;"
```

Statuses:

| Status | Meaning |
|--------|---------|
| `farmed` | Tokens in DB, not yet on 9router |
| `injected` | Present on 9router (grok-cli path) |
| `error` | Failed inject or expired token (`notes`) |

---

## 6. Batch artifacts

```
results/
  used_emails.txt
  batch_<YYYYMMDD_HHMMSS>_<id>/
    accounts.txt      # email|password|access|refresh|ts
    accounts.json
    farm.log
    batch_meta.json
screenshots/          # debug captures
logs/s3_backup.log    # backup timer
```

Retention: operator policy (disk is cheap; tokens are sensitive — encrypt backups).

---

## 7. Backup & restore

### Automated S3 (encrypted)

```bash
# Requires: age, backup.env, age.pubkey or AGE_RECIPIENT
~/grok-farm/scripts/s3_backup.sh
# → s3://BUCKET/PREFIX/HOST/DATE/grok-farm-HOST-STAMP.tgz.age
# → .../latest.tgz.age + LATEST.txt
# then: s3_upload.py --retention (boto3; RETENTION_DAYS default 14)
# log: ~/grok-farm/logs/s3_backup.log
```

Retention keeps `latest.tgz.age` + `LATEST.txt`; deletes dated objects older than `RETENTION_DAYS` under `S3_PREFIX/HOST/`. Manual:

```bash
set -a; source ~/.config/grok-farm/backup.env; set +a
export HOST_NAME="$(hostname -s)"
python3 ~/grok-farm/scripts/s3_upload.py --retention --dry-run
python3 ~/grok-farm/scripts/s3_upload.py --retention
```

See RUNBOOK **R12**.

### Operator pull backup

```bash
./scripts/backup_farm.sh magadirxwin@FARM_IP ./backups age1...
# produces .tgz.age when recipient set
```

### Restore farmer DB

```bash
# Encrypted
./scripts/restore_farm.sh magadirxwin@FARM_IP ./backups/xxx.tgz.age ~/.config/grok-farm/age.identity

# Manual (stops farmer — confirm first)
systemctl stop grok-farmer
cp akun.db akun.db.bak
# extract credentials/akun.db from decrypted payload
chown magadirxwin:magadirxwin ~/grok-farm/akun.db
chmod 600 ~/grok-farm/akun.db
systemctl start grok-farmer
```

See [SECURITY.md](./SECURITY.md) for age key management and [MIGRATION.md](./MIGRATION.md).

---

## 8. Capacity guidance (8 GB RAM / 4 vCPU)

| Concurrent | Notes |
|------------|--------|
| 1–2 | Safest |
| 3 | Recommended with 8G swap |
| 4 | Possible; watch Turnstile fail rate + OOM |

See [CAPACITY.md](./CAPACITY.md).

---

## 9. Security ops

- Never run farmer as root
- Rotate Gmail App Password if leaked
- Restrict SSH key usage (from/to CIDR if possible)
- Do not expose 9router dashboard without auth
- Treat `accounts.txt` / `akun.db` as **credential vault**
- No `NOPASSWD:ALL` on farmer user
- Offsite backups must be `.tgz.age`, not plaintext

---

## 10. Alerts

If `GROK_ALERT_WEBHOOK` (or `GROK_FARM_ALERT_WEBHOOK`) is set, `workflow.py` / `alerts.py` POST redacted Discord-style messages on empty proxy pool, all-fail inject, partial inject, or errors.

- **Leave unset** if no Discord/Telegram webhook — do not invent a URL.
- Bodies are redacted (no full JWT / proxy credentials).
- Optional future: systemd timer that runs `check_status.py` and alerts on exit 2 (not required for steady state).

---

## 11. Escalation

| Symptom | First action | Doc |
|---------|--------------|-----|
| Service dead | `systemctl status` + journal | [RUNBOOK.md](./RUNBOOK.md) |
| 0 new accounts | farm.log Turnstile/IMAP | RUNBOOK |
| Inject backlog | `workflow.py` manual + SSH | [INTEGRATION-9ROUTER.md](./INTEGRATION-9ROUTER.md) |
| Grok 4.5 region 403 | proxy pool non-EU | RUNBOOK geo |
| Backup stale | `logs/s3_backup.log` + age/S3 creds | SECURITY |
| Many expired tokens | `check_status` token_health; re-farm | DATA-MODEL |
