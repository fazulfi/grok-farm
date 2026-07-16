# Operations Guide — Grok Farm

**Audience:** operators / on-call  
**Environment:** Linux VPS (Ubuntu 24.04+), systemd, non-root farmer user

---

## 1. Service inventory

| Service | Host | Unit / process | Purpose |
|---------|------|----------------|---------|
| `grok-farmer` | Each farm VPS | `systemd` (`brutal_farmer.sh` **v5**) | Unlimited farm → import → inject loop |
| Camoufox | Each farm VPS | child of `farm.py` | Browser automation |
| 9router | Gateway VPS | `next-server` / custom | LLM gateway + proxyPools |
| SSH tunnel path | Farm → Gateway | OpenSSH key | Proxy sync + inject |
| `grok-farm-backup.timer` | Each farm VPS | systemd timer | Hourly age-encrypted backup: **S3** when `~/.config/grok-farm/backup.env` present; else local `~/grok-farm/backups/` |
| `grok-farm-health.timer` | Each farm VPS | systemd timer | Every **15 min** (`*:0/15` + RandomizedDelay ≤2m): inventory `check_status.py --json` via `scripts/health_check.sh`; webhook on **hard** exit **3** only (soft exit 2 = log-only) |
| `grok-farm-probe.timer` | Each farm VPS | systemd timer | Every **6h**: soft `probe_tokens.py` (`scripts/probe_soft.sh`, no `--mark-error`) |
| `grok-farm-reconcile.timer` | Each farm VPS | systemd timer | Every **12h**: `reconcile_9router.py --json` report only |
| `grok-farm-mark-expired.timer` | Each farm VPS | systemd timer | Every **30 min**: mark **farmed-only** expired JWTs as `error` (no `--include-injected`) |
| `grok-farm-digest.timer` | Each farm VPS | systemd timer | Daily **~01:00 UTC** (+ ≤5m random): `daily_digest.py` → **one fleet Telegram** (S3 snapshots + leader) or `--local` per-host; inventory only |

### Fleet hosts (2026-07-16 rebuild)

| Tag | IP | Concurrent | Mid-drain | Notes |
|-----|-----|------------|-----------|-------|
| grok2 | `157.245.55.62` | **3** | off | S3 `farm-vps/grok2` |
| grok3 | `168.144.36.46` | **3** | off | fleet digest **leader**; `farm-vps/grok3` |
| grok4 | `104.248.157.41` | **3** | 120s | mid-drain host; `farm-vps/grok4` |
| grok5 | `168.144.37.202` | **3** | off | `farm-vps/grok5` |
| grok6 | `167.71.208.99` | **3** | off | `farm-vps/grok6` |

OTP domains (live, 3): `markettabrak.biz.id`, `markettabrak.my.id`, `markettabrak.site` (Pattern B multi-IMAP).

User/app on all farmers: `magadirxwin` / `/home/magadirxwin/grok-farm`. Gateway: `49.12.82.34:39999`. Full multi-VPS rules: [CAPACITY.md](./CAPACITY.md) §6.2.

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

# Timer path (inventory only; no --probe/--mark-error)
bash scripts/health_check.sh
systemctl status grok-farm-health.timer --no-pager
tail -30 ~/grok-farm/logs/health_check.log

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

`check_status.py` reports: farmer unit, account counts, token health, **live probe**
(`needs_relogin` + last_probe_status), proxy file lines, top `proxy_stats` (incl.
soft-skip), domain pool / `domain_stats`, disk, last backup log age.

**Exit codes (hard/soft split):**

| Exit | Meaning | Health timer webhook |
|------|---------|----------------------|
| **0** | Healthy (no issues) | none |
| **2** | Soft-only (`needs_relogin`, `many_expired_tokens`, `proxy_pool_low`, `proxy_many_soft_skipped`, …) | **log-only** (no webhook) |
| **3** | Hard (`farmer_not_active`, `proxy_file_empty`, `disk_high`, backlog, backup_*, identity_*, …) | **webhook** if set |

JSON includes `issues_hard`, `issues_soft`, `exit_code`, `proxy_soft_skip`.

```bash
python3 check_status.py
python3 check_status.py --json
# Soft inventory probe line: needs_relogin count + last_probe_status breakdown
# (soft policy: meta only; does not flip injected→error)

# Large soft probe batch (default soft; no --mark-error)
python3 probe_tokens.py --limit 200
python3 probe_tokens.py --limit 200 --json
# Timer path: bash scripts/probe_soft.sh  (GROK_PROBE_TIMER_LIMIT default 100)

# Smaller sample / dry-run
python3 check_status.py --probe --probe-limit 20
python3 probe_tokens.py --limit 50
python3 probe_tokens.py --dry-run --limit 5

# Mark dead farmed JWTs as status=error (same as mark_expired_tokens.py)
python3 check_status.py --mark-expired
# Optional: also mark injected expired as error (inventory hygiene; no gateway revoke)
python3 check_status.py --mark-expired-injected
# Timer path (farmed-only): bash scripts/mark_expired_farmed.sh

python3 mark_expired_tokens.py --dry-run
python3 mark_expired_tokens.py

# Gateway vs DB inventory (no tokens printed)
python3 reconcile_9router.py
python3 reconcile_9router.py --json
python3 reconcile_9router.py --json --limit-print 5
# Timer path: bash scripts/reconcile_soft.sh

# Adaptive concurrent decision for next farm batch
python3 adaptive_concurrent.py --print
python3 adaptive_concurrent.py --json
```

**Soft inventory (probe) dashboard:** `check_status.py` prints
`probe: needs_relogin=N last_status={...}` and may list soft issue `needs_relogin`
(exit **2**). Use `probe_tokens.py --limit 200 --json` for batch counts by `alive` /
`needs_relogin` / `jwt_expired` / network. Soft policy = write `last_probe_*` +
`needs_relogin` only; never `status=error` unless explicit `--mark-error`.

See RUNBOOK **R10** (expired JWT), **R13** (probe, large batch), **R14**
(reconcile), **R15** (adaptive concurrent), **R17** (health hard/soft), **R18**
(proxy soft-evict).

### 2.2 Expected steady state

Product model = **autofarm + auto-inject** (not session management). After inject, token alive/dead is 9router/consumer concern; soft probe/JWT noise on farm is optional.

- `grok-farmer`: **active (running)**
- Concurrent Camoufox parents ≈ adaptive `CONCURRENT` (base 3, bounds 1–5 when `GROK_ADAPTIVE_CONCURRENT=1`)
- `workflow_pools` / `usa_proxies.txt` line count ≈ proxyPools count
- `farmed` backlog near **0** after each successful inject cycle (on-path inject; no backlog “session queue”)
- Soft `needs_relogin` / offline JWT % may be high without meaning farm failure
- Swap present if concurrent ≥ 3
- Backup log age &lt; ~2h when timer enabled; objects on S3 end with `.tgz.age`- `akun.db` mode 600

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
# Example: live production pool (update when domains change)
grep -q '^GROK_EMAIL_DOMAINS=' .env \
&& sed -i 's|^GROK_EMAIL_DOMAINS=.*|GROK_EMAIL_DOMAINS=markettabrak.biz.id,markettabrak.my.id,markettabrak.site|' .env \
|| echo 'GROK_EMAIL_DOMAINS=markettabrak.biz.id,markettabrak.my.id,markettabrak.site' >> .env
# Keep legacy key in sync (optional)
sed -i 's|^GROK_EMAIL_DOMAIN=.*|GROK_EMAIL_DOMAIN=markettabrak.my.id|' .env
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
tail -f results/batch_*/farm.log | grep -E 'start|wait_otp|OK|FAIL|markettabrak'
```

#### Domain health auto-skip

- Table `domain_stats` (see DATA-MODEL): updated on each farm success/fail from `farm.py`.
- After `GROK_DOMAIN_MAX_CONSECUTIVE_FAILS` (default 3) consecutive fails, domain is skipped on pick.
- If **all** pool domains would be skipped → fail-open (still pick from full pool).
- Re-enable after routing fix: RUNBOOK **R3d** SQL or remove domain from pool until IMAP probe green.
- Do **not** assume Cloudflare UI “Active” means OTP inbox receives xAI mail — always verify with IMAP probe (R3c).

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
- Next farmer loop runs `sync_proxies_from_9r.py` (gateway `list_proxies.py`
  soft-filters `isActive=0` / bad `testStatus` when present; **fail-open** if
  filter would empty the pool)
- **Inject** uses live pool list with **score-weighted pick** + soft-skip
  (`workflow.pick_proxy` + `proxies_to_skip()`; **fail-open** if all skipped)
- **Farm** uses local file with same soft-skip + score-weight (`farm.next_proxy`;
  **fail-open**); never empties pool via soft skip alone
- Inject/farm success/fail → `proxy_stats` (host-only key; `last_fail_reason`
  taxonomy; mirrors domain auto-skip). **Never** auto-DELETE gateway `proxyPools`
- Empty local proxy file after sync → `brutal_farmer` **skips farm** that round
  (inject remains fail-closed on empty live proxyPools)

Do **not** hand-edit `usa_proxies.txt` for production; it is overwritten by sync.

---

## 5. Account inventory (pipeline marks)

`akun.db` is **pipeline bookkeeping** for farm → inject, not a session manager. Health/probe tools are observability; they do not reauth or heal gateway sessions.

```bash
# Summary + token health + proxy scores
python3 ~/grok-farm/check_status.py

# SQL
sqlite3 ~/grok-farm/akun.db \
  "SELECT status, COUNT(*) FROM accounts GROUP BY status;"
sqlite3 ~/grok-farm/akun.db \
  "SELECT token_health, COUNT(*) FROM accounts GROUP BY token_health;"
sqlite3 ~/grok-farm/akun.db \
  "SELECT proxy_key, success_count, fail_count, consecutive_fails, disabled, score, last_fail_reason \
   FROM proxy_stats ORDER BY score DESC LIMIT 10;"
```

Statuses (inject-queue marks only):

| Status | Meaning |
|--------|---------|
| `farmed` | Tokens in DB, waiting on-path inject (steady state ≈ 0) |
| `injected` | Handoff to 9router succeeded — farm no longer owns session validity |
| `error` | Pipeline failure or dead farmed JWT (`notes`); capacity recovery = re-farm + inject |

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
# → s3://BUCKET/HOST_ROOT/DATE/grok-farm-HOST-STAMP.tgz.age
# HOST_ROOT = S3_PREFIX if it already ends with host key (e.g. farm-vps/grok4),
# else S3_PREFIX/HOST (e.g. farm-vps + grok4 → farm-vps/grok4). No double host.
# → .../latest.tgz.age + LATEST.txt
# then: s3_upload.py --retention (boto3; RETENTION_DAYS default 14)
# log: ~/grok-farm/logs/s3_backup.log
```

Retention keeps `latest.tgz.age` + `LATEST.txt`; deletes dated objects older than `RETENTION_DAYS` under host root (`farm-vps/grokN/`). Manual:

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

`alerts.py` fires when **Telegram** (`GROK_TELEGRAM_BOT_TOKEN` +
`GROK_TELEGRAM_CHAT_ID`) and/or Discord-style webhook (`GROK_ALERT_WEBHOOK` /
`GROK_FARM_ALERT_WEBHOOK`) is set.

| Source | When | Detail |
|--------|------|--------|
| `farm.py` end-of-batch | every farm run (ok / partial / all-fail / empty) | Host + batch_id + **full email lists** ok/fail (+ fail reason); HUD remains primary UI; `skip_debounce=True` |
| `workflow.py` inject | empty pool, all-fail, partial, full ok, error | Host + email lists + fail_class + redacted proxy; `skip_debounce=True` |
| Health timer | exit **3** hard only | Inventory issues; soft exit 2 = log-only |

- **Leave unset** if unused — do not invent a URL or token.
- Bodies are redacted (**no** full JWT / password / proxy credentials / bot token).
  Account **emails** + status are allowed for operator visibility.
- **Debounce:** same title+issues fingerprint suppressed for
  `GROK_ALERT_DEBOUNCE_MIN` minutes (default **60**; `0` = off) via
  `logs/alert_debounce/<hash>.ts`. Per-batch farm/inject alerts use
  `skip_debounce=True` so each batch is visible.
- **Health timer:** `grok-farm-health.timer` → `scripts/health_check.sh` →
  `check_status.py --json` every 15 minutes (inventory only; **no** `--probe` /
  `--mark-error`). Alert on exit **3** (hard) or unexpected failure only; exit
  **2** (soft) is log-only. Unit `SuccessExitStatus=2 3`. Log:
  `logs/health_check.log`.
- **Daily digest + proxy dashboard (fleet-wide):** `grok-farm-digest.timer`
  (~01:00 UTC + ≤5m random) → `scripts/daily_digest.sh` → `daily_digest.py`.
  Default: each host uploads a redacted snapshot to
  `s3://…/farm-vps/fleet-digest/YYYY-MM-DD/<host>.json` (uses `backup.env` S3);
  **leader** waits for peers then posts/updates **one sticky** Telegram fleet
  card (`editMessageText` via `send_or_edit_sticky`; state
  `~/.config/grok-farm/telegram_sticky_fleet_digest.json`). Followers do not
  send Telegram. Per-host card: `python daily_digest.py --local`. Manual:
  `--dry-run` / `--proxy-only` / `--fleet` / `--json` / `--no-wait`. Log:
  `logs/daily_digest.log`. Env: `GROK_HOST_TAG`, `GROK_FLEET_DIGEST*`,
  `GROK_FLEET_DIGEST_STICKY` (see `.env.example`). RUNBOOK **R20**.
- **Proxy re-enable CLI:** `python reenable_proxy.py --list|--match|--all`
  clears local soft-skip only (never gateway DELETE). RUNBOOK **R18**.
- **S3 backup:** each host has `~/.config/grok-farm/backup.env` + `age.pubkey`;
  hourly `local_age_backup.sh` → age encrypt → upload under
  `s3://grok-farm/farm-vps/grok{N}/…`.

### Soft observability timers (no session recovery)

| Timer | Script | Notes |
|-------|--------|-------|
| `grok-farm-probe.timer` | `scripts/probe_soft.sh` | Soft probe; never `--mark-error` |
| `grok-farm-reconcile.timer` | `scripts/reconcile_soft.sh` | Diff report only |
| `grok-farm-mark-expired.timer` | `scripts/mark_expired_farmed.sh` | farmed JWT → `error` only |
| `grok-farm-digest.timer` | `scripts/daily_digest.sh` | Telegram daily digest + proxy dashboard |

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
