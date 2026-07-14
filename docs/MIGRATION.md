# Migration Playbook — VPS Death / Rebuild

**Audience:** on-call / operator  
**Last proven:** 2026-07-14 — multi-VPS DO fleet (grok3–grok7) with age/S3 per-host prefixes; CSA `168.144.137.240` **retired**  
**Historical:** 2026-07-13 — old CSA `152.42.242.192` → `168.144.137.240` (now retired)  
**RTO target:** < 45 min with age backup; < 90 min rebuild from 9router only  
**RPO:** last successful inject to 9router (tokens live on gateway even if farm disk is gone)

---

## 0. Read this first

| Survives farm VPS death? | Asset |
|--------------------------|--------|
| **Yes** | GitHub `fazulfi/grok-farm` (code) |
| **Yes** | 9router gateway (`proxyPools` + `providerConnections` tokens) |
| **Yes** | Cloudflare Email Routing + domains |
| **Yes** | Gmail App Passwords (operator vault) |
| **Maybe** | Laptop `age.identity` + local copies of secrets |
| **Maybe** | Hourly `~/grok-farm/backups/*.tgz.age` (if you pull off-host) |
| **Lost if only on dead disk** | Live `.env`, `identities.json`, `akun.db`, farm SSH private key |

**Golden rule:** tokens that already sit in 9router as `grok-cli` can rebuild `akun.db`. Farming secrets (IMAP / Grok password / identities) **cannot** be invented — keep them in a password manager + optional age archive.

Related: [DEPLOYMENT.md](./DEPLOYMENT.md) · [RUNBOOK.md](./RUNBOOK.md) R16 · [SECURITY.md](./SECURITY.md) · [OPERATIONS.md](./OPERATIONS.md)

---

## 1. Live production map (update when host changes)

| Role | Current (2026-07-14) | Notes |
|------|----------------------|--------|
| Farm fleet | **grok4** `157.245.49.4` · **grok3** `143.198.86.242` · **grok5** `206.189.37.233` · **grok6** `174.138.24.143` · **grok7** `157.245.199.70` | DO SGP1; 8G concurrent **3** (mid-drain grok4 only); grok6 4G light concurrent **1** |
| Farm user | `magadirxwin` (non-root) | Camoufox **must not** run as root |
| App dir | `/home/magadirxwin/grok-farm` | per host |
| DB | `~/grok-farm/akun.db` mode **600** | **per-host** (not shared) |
| Gateway | `49.12.82.34` SSH port **39999** | 9router Pro; API often `:20128` |
| Proxy SoT | 9router **`proxyPools`** | Sync → `usa_proxies.txt` |
| Backup S3 | `s3://grok-farm/farm-vps/grok{N}/…` | age encrypt; `backup.env` per host |
| Domains | `budgezen.com` + `mypapyr.com` | Pattern B → two Gmails |
| Code pin | tag **`v2.3.2`+** / `main` | Prefer release tag |

Retired / do not target as production: CSA `168.144.137.240`, old `152.42.242.192` (`csa-old`).

---

## 2. Critical assets checklist (before disaster)

| Asset | Criticality | Offline copy required | Where to keep |
|-------|-------------|----------------------|---------------|
| Git repo | Code | GitHub | `fazulfi/grok-farm` private |
| `.env` | **SEV0** | Password manager | Never git |
| `identities.json` | **SEV0** multi-Gmail | Password manager | VPS `chmod 600` only |
| Gmail App Passwords | **SEV0** | Password manager | Primary + secondary |
| `GROK_PASSWORD` | **SEV0** | Password manager | Quote if contains `#` |
| `akun.db` | **SEV0 inventory** | age backup / 9router rebuild | |
| Farm→9router SSH key | High | Operator keystore | `~/.ssh/id_ed25519` on farmer |
| `age.identity` | High (decrypt) | **Laptop** `~/.config/grok-farm/age.identity` | Root-only on VPS too |
| `age.pubkey` | Medium | Same folder | On VPS for encrypt |
| `backup.env` (S3) | Medium | Password manager | Optional offsite |
| Domains CF routing | High | CF dashboard | Independent of VPS |
| 9router host | High | Independent | proxyPools + tokens |

### 2.1 Minimum vault (operator laptop)

```text
~/.config/grok-farm/
  age.identity          # decrypt backups (ACL locked to your user)
  age.pubkey
Password manager:
  Gmail app passwords (primary + secondary)
  GROK_PASSWORD
  Root/SSH password (or keys only)
  Optional: S3 keys for is3.cloudhost.id
  Optional: copy of identities.json structure (passwords redacted in notes)
```

### 2.2 What to pull off-VPS weekly

```bash
# From workstation with SSH csa
scp csa:/home/magadirxwin/grok-farm/backups/latest.tgz.age ./backups/
# Decrypt only when needed:
# age -d -i ~/.config/grok-farm/age.identity -o restore.tgz latest.tgz.age
```

---

## 3. Decision tree: which restore path?

```
Farm VPS dead?
├─ Still have latest.tgz.age (or S3 .tgz.age) + age.identity
│    → PATH A: restore encrypted backup (fastest full secrets)
├─ No farm backup, but 9router still has grok-cli connections
│    → PATH B: rebuild akun.db from 9router + re-enter secrets
├─ Old disk still SSH-able briefly
│    → PATH C: warm rsync/backup then cutover
└─ Nothing (no backup, no 9router tokens)
     → PATH D: cold farm only (empty inventory; re-farm from zero)
```

---

## 4. PATH A — Restore from age backup (preferred)

### A.1 Decrypt on workstation (or any Linux with age)

```bash
age -d -i ~/.config/grok-farm/age.identity \
  -o /tmp/grok-farm-restore.tgz \
  ./backups/latest.tgz.age

mkdir -p /tmp/grok-restore && tar -tzf /tmp/grok-farm-restore.tgz | head
tar -xzf /tmp/grok-farm-restore.tgz -C /tmp/grok-restore
# Expect: credentials/.env, credentials/akun.db, identities.json, etc. (layout may vary)
```

Local age packager: `scripts/local_age_backup.sh` (hourly timer).  
S3 path (if keys present): `scripts/s3_backup.sh` → `s3://grok-farm/farm-vps/<host>/latest.tgz.age`.

### A.2 Provision new VPS + farmer user

Follow [DEPLOYMENT.md](./DEPLOYMENT.md) §2 **with least privilege** (do **not** grant `NOPASSWD:ALL`):

```bash
# As root on NEW VPS
export FARM_USER=magadirxwin
adduser --disabled-password --gecos "" "$FARM_USER"
usermod -aG sudo "$FARM_USER"
# Limited sudoers only (copy from live or create):
cat >/etc/sudoers.d/grok-farmer-$FARM_USER <<EOF
$FARM_USER ALL=(root) NOPASSWD: /bin/systemctl start grok-farmer, /bin/systemctl stop grok-farmer, /bin/systemctl restart grok-farmer, /bin/systemctl status grok-farmer, /bin/systemctl is-active grok-farmer, /bin/systemctl reload grok-farmer, /bin/journalctl -u grok-farmer *
EOF
chmod 440 /etc/sudoers.d/grok-farmer-$FARM_USER

# Packages
apt-get update
apt-get install -y python3 python3-venv python3-pip git curl wget unzip \
  xvfb libgtk-3-0 libx11-xcb1 age rsync sqlite3

# 8G swap (required for Camoufox concurrency)
fallocate -l 8G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab
echo 'vm.swappiness=10' >> /etc/sysctl.conf && sysctl vm.swappiness=10
```

### A.3 Code + runtime

```bash
# Prefer tag
sudo -u magadirxwin -i
cd ~
git clone --branch v2.2.0 https://github.com/fazulfi/grok-farm.git grok-farm
# or: git clone --branch main ...
cd grok-farm
chmod +x install.sh run.sh brutal_farmer.sh scripts/*.sh
./install.sh   # venv + Camoufox + GeoIP + uBlock — takes several minutes
```

Workstation alternate: `./scripts/deploy_farm_vps.sh magadirxwin@NEW_IP --branch main`

### A.4 Restore secrets from decrypted tarball

```bash
# Paths inside tarball depend on packager; common layouts:
#   credentials/.env  credentials/akun.db  identities.json
#   or flat .env akun.db

sudo -u magadirxwin bash -lc '
  cd ~/grok-farm
  # adjust SRC after inspecting tar
  cp /tmp/restore/.env .env
  cp /tmp/restore/akun.db akun.db
  test -f /tmp/restore/identities.json && cp /tmp/restore/identities.json .
  chmod 600 .env akun.db identities.json 2>/dev/null || true
  touch farm_brutal.log workflow.log
  chown magadirxwin:magadirxwin farm_brutal.log workflow.log
'
```

**Quote shell-special values in `.env`:**

```bash
# BAD (bash source breaks on #):
GROK_PASSWORD=4Dj@w!cqZ9d&R0#3hDT4
# GOOD:
GROK_PASSWORD='4Dj@w!cqZ9d&R0#3hDT4'
```

### A.5 age keys on VPS

```bash
# Encrypt pubkey for farmer backups
install -d -m 700 /home/magadirxwin/.config/grok-farm
# copy age.pubkey → /home/magadirxwin/.config/grok-farm/age.pubkey (644)
# Private identity: root-only (decrypt DR)
install -d -m 700 /root/.config/grok-farm
# copy age.identity → /root/.config/grok-farm/age.identity (600)
```

### A.6 Farm → 9router SSH

```bash
# As magadirxwin
ssh-keygen -t ed25519 -N "" -f ~/.ssh/id_ed25519   # or restore private key from vault
# On 9router (root):
#   echo 'ssh-ed25519 AAAA... magadirxwin@farm' >> ~/.ssh/authorized_keys

# Test (port 39999 production):
ssh -p 39999 -i ~/.ssh/id_ed25519 -o BatchMode=yes root@49.12.82.34 'echo 9R_OK'
```

`.env` keys (aliases supported by `workflow.py`):

```bash
GROK_9R_SSH=root@49.12.82.34
GROK_9R_PORT=39999
GROK_9R_KEY=/home/magadirxwin/.ssh/id_ed25519
# also accepted: GROK_9R_SSH_KEY / GROK_9R_SSH_PORT
```

### A.7 Gateway helpers (once per 9router)

```bash
# From repo, on 9router as root
cp ops/list_proxies.py /root/list_proxies.py
cp ops/grok_cli_bulk_inject.py /root/grok_cli_bulk_inject.py
cp ops/list_grok_connections.py /root/list_grok_connections.py
cp ops/export_9r_to_akun.py /root/export_9r_to_akun.py
chmod +x /root/*.py
```

### A.8 Proxies + systemd + start

```bash
cd ~/grok-farm
source .venv/bin/activate
python3 sync_proxies_from_9r.py    # expect ~65 lines → usa_proxies.txt
# Concurrent bounds (production defaults)
# GROK_CONCURRENT=3 GROK_CONCURRENT_MIN=1 GROK_CONCURRENT_MAX=3 GROK_ADAPTIVE_CONCURRENT=1

# Install units (as root)
sed "s/magadirxwin/$USER/g; s|/home/magadirxwin|$HOME|g" systemd/grok-farmer.service \
  | sudo tee /etc/systemd/system/grok-farmer.service
sed "s/magadirxwin/$USER/g; s|/home/magadirxwin|$HOME|g" systemd/grok-farm-backup.service \
  | sudo tee /etc/systemd/system/grok-farm-backup.service
sudo cp systemd/grok-farm-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now grok-farm-backup.timer
sudo systemctl enable --now grok-farmer

python3 check_status.py
# farmer=active, proxies>0, farmed backlog drain via brutal v5 mid-batch
```

---

## 5. PATH B — No farm backup (rebuild from 9router) — **proven 2026-07-13**

Used when old VPS is suspended and laptop has **no** `backup.env` / no `akun.db`, but 9router still holds tokens.

### B.1 Export inventory on 9router

```bash
# SSH to 9router
scp ops/export_9r_to_akun.py root@GATEWAY:/root/   # or use already deployed copy
ssh -p 39999 root@GATEWAY 'python3 /root/export_9r_to_akun.py /tmp/akun_rebuild.db'
# Expect: INSERTED N, TOTAL N (e.g. 624)
scp -P 39999 root@GATEWAY:/tmp/akun_rebuild.db ./akun_rebuild.db
```

### B.2 Bootstrap new farm (same as A.2–A.3)

Code install + swap + non-root user + limited sudoers + Camoufox.

### B.3 Place rebuilt DB

```bash
scp akun_rebuild.db magadirxwin@NEW:/home/magadirxwin/grok-farm/akun.db
ssh magadirxwin@NEW 'chmod 600 ~/grok-farm/akun.db; chown magadirxwin:magadirxwin ~/grok-farm/akun.db'
```

Notes after rebuild:

- `status=injected`, `batch_id=rebuild_from_9router`
- Offline JWT may show many `token_health=expired` (soft issue only)
- Passwords may be empty if not stored in gateway JSON

### B.4 Re-enter secrets (cannot recover from 9router alone)

Create `~/grok-farm/.env` from `.env.example` with **real** values:

| Key | Purpose |
|-----|---------|
| `GROK_IMAP_USER` / `GROK_IMAP_PASS` | Primary Gmail App Password |
| `GROK_EMAIL_DOMAINS` | e.g. `budgezen.com,mypapyr.com` |
| `GROK_EMAIL_DOMAIN_STRATEGY` | `round_robin` recommended |
| `GROK_PASSWORD` | **Quoted** farm password for xAI signup |
| `GROK_HEADLESS=true` | VPS |
| `GROK_CONCURRENT=3` `GROK_CONCURRENT_MAX=3` `GROK_ADAPTIVE_CONCURRENT=1` |
| `GROK_PROXY_FILE=~/grok-farm/usa_proxies.txt` | After sync |
| `GROK_9R_SSH` `GROK_9R_PORT` `GROK_9R_KEY` | Gateway |

Pattern B multi-Gmail — `identities.json` (chmod 600, **never git**):

```json
{
  "identities": [
    {
      "id": "primary",
      "imap_user": "primary@gmail.com",
      "imap_pass": "xxxx xxxx xxxx xxxx",
      "imap_host": "imap.gmail.com",
      "imap_port": 993,
      "domains": ["budgezen.com"],
      "enabled": true
    },
    {
      "id": "secondary",
      "imap_user": "secondary@gmail.com",
      "imap_pass": "yyyy yyyy yyyy yyyy",
      "imap_host": "imap.gmail.com",
      "imap_port": 993,
      "domains": ["mypapyr.com"],
      "enabled": true
    }
  ]
}
```

Verify IMAP before starting farmer:

```bash
# On VPS — expect OK inbox counts (do not log passwords)
python3 - <<'PY'
import imaplib, os
from pathlib import Path
# load identities or .env yourself; login SELECT INBOX
print("manual IMAP check — use private one-liner with env vars")
PY
```

### B.5 SSH, proxies, start (same as A.6–A.8)

```bash
python3 sync_proxies_from_9r.py
python3 adaptive_concurrent.py --print   # expect ≤ GROK_CONCURRENT_MAX
python3 probe_tokens.py --limit 100      # soft inventory; no mark-error
python3 reconcile_9router.py             # expect large in_both_ok
sudo systemctl enable --now grok-farmer
```

### B.6 First farm batch validation

```bash
tail -f ~/grok-farm/farm_brutal.log
# OTP both domains, Turnstile, OAuth save
# brutal v5: mid-drain every 120s → SUMMARY N ok 0 fail
python3 check_status.py
# farmed=0 after drain; injected increases; farmer=active
```

---

## 6. PATH C — Warm migration (old VPS still SSH-able)

```bash
# Workstation
./scripts/backup_farm.sh magadirxwin@OLD_IP ./backups
# Prefer age-encrypted if AGE_RECIPIENT set
./scripts/deploy_farm_vps.sh magadirxwin@NEW_IP --branch main
./scripts/restore_farm.sh magadirxwin@NEW_IP ./backups/grok-farm-backup-XXXX.tgz

ssh magadirxwin@NEW_IP 'cd ~/grok-farm && python3 sync_proxies_from_9r.py && python3 workflow.py && sudo systemctl enable --now grok-farmer'
ssh magadirxwin@OLD_IP 'sudo systemctl disable --now grok-farmer'
```

Keep old disk 7 days offline for rollback.

---

## 7. PATH D — Empty cold start (no inventory)

Only when gateway also lost tokens:

1. DEPLOYMENT §2 install  
2. Fresh `.env` + `identities.json`  
3. Empty `akun.db` (created on first import)  
4. Sync proxies + start farmer  
5. Re-farm from zero  

---

## 8. Post-restore validation gate (mandatory)

| # | Check | Pass criteria |
|---|-------|---------------|
| 1 | User | `whoami` as farmer ≠ root; Camoufox not root |
| 2 | Swap | `swapon --show` ≥ 4G |
| 3 | Service | `systemctl is-active grok-farmer` → **active** |
| 4 | Logs writable | `ls -l workflow.log farm_brutal.log` owned by farmer (not root) |
| 5 | Brutal version | `grep 'Brutal Farmer v' farm_brutal.log` → **v5** |
| 6 | Proxies | `wc -l usa_proxies.txt` > 0; SoT = proxyPools |
| 7 | Identity | `check_status` shows domains + ids (Pattern B: ids=2) |
| 8 | IMAP | OTP arrives for each domain in pool |
| 9 | Inject | `grep SUMMARY farm_brutal.log` / mid-drain works |
| 10 | DB | `farmed=0` after drain; no inject desync |
| 11 | Gateway | `reconcile_9router.py` in_both_ok grows |
| 12 | Backup | `check_status` backup=ok (local age or S3) |
| 13 | Sudo | No `NOPASSWD:ALL` for farmer |
| 14 | Secrets | `.env` `identities.json` `akun.db` mode **600** |

```bash
cd ~/grok-farm
python3 check_status.py
python3 reconcile_9router.py --limit-print 5
python3 adaptive_concurrent.py --print
grep -E 'SUMMARY|Permission denied|Brutal Farmer v|mid-drain' farm_brutal.log | tail -20
```

### Known failure: auto inject Permission denied

If `workflow.log` is root-owned (common after root debugging):

```bash
sudo chown magadirxwin:magadirxwin ~/grok-farm/workflow.log ~/grok-farm/farm_brutal.log
# brutal v5 also falls back to workflow_user.log if unwritable
```

See OPERATIONS § Auto import/inject (brutal v5).

---

## 9. Backup (S3 offsite preferred + local fallback)

### 9.1 Offsite S3 (production SoT for DR)

| Item | Value |
|------|--------|
| Endpoint | `https://is3.cloudhost.id` |
| Bucket | `grok-farm` |
| Prefix (live) | `farm-vps/grok{N}/` per host (e.g. `farm-vps/grok4`); script does **not** append host again when `S3_PREFIX` already ends with host key |
| Objects | dated `*.tgz.age`, `latest.tgz.age`, `LATEST.txt` |
| Secrets file | `~/.config/grok-farm/backup.env` (mode **600**, farmer user only) |
| Encrypt | `BACKUP_ENCRYPT=age` + `~/.config/grok-farm/age.pubkey` |
| Timer | `grok-farm-backup.timer` hourly → `local_age_backup.sh` **delegates to** `s3_backup.sh` when `backup.env` exists |
| Upload | `scripts/s3_upload.py` (boto3, ContentLength) |
| Retention | `RETENTION_DAYS=14` (keeps `latest.*` + `LATEST.txt`) |

**Manual run (farmer user):**

```bash
bash ~/grok-farm/scripts/s3_backup.sh
# or (same when backup.env present):
bash ~/grok-farm/scripts/local_age_backup.sh
tail -30 ~/grok-farm/logs/s3_backup.log
```

**Restore PATH A from S3:** download `latest.tgz.age` → decrypt with laptop `age.identity` → `scripts/restore_farm.sh` (see PATH A above).

### 9.2 Local age fallback (no S3 keys)

When `backup.env` is missing:

| Unit | Role |
|------|------|
| `scripts/local_age_backup.sh` | Pack + age encrypt → `~/grok-farm/backups/*.tgz.age` |
| `grok-farm-backup.timer` | Hourly |
| Log | `logs/s3_backup.log` (health CLI reads this name) |

Requires `~/.config/grok-farm/age.pubkey` (farmer) and identity offline for decrypt.

**Enable S3:** write `~/.config/grok-farm/backup.env` (600) with `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `S3_ENDPOINT`, `S3_BUCKET=grok-farm`, `S3_PREFIX=farm-vps/grokN` (match host tag), `BACKUP_ENCRYPT=age`; install `boto3` in farm `.venv`; next timer run uploads offsite.

---

## 10. DNS / email (usually no change)

| Item | Action on farm VPS death |
|------|---------------------------|
| Cloudflare catch-all | None |
| Pattern B destinations | None (Gmail side) |
| New domain | CF routing + `identities.json` + R11 |

If OTP fails after restore: RUNBOOK **R3 / R3c** (probe IMAP To: headers).

---

## 11. Workstation SSH config template

```sshconfig
Host csa
  HostName 168.144.137.240
  User root
  IdentityFile ~/.ssh/id_ed25519
  # farmer ops: ssh csa then sudo -u magadirxwin -i

Host csa-farmer
  HostName 168.144.137.240
  User magadirxwin
  IdentityFile ~/.ssh/id_ed25519

Host ninerouter
  HostName 49.12.82.34
  Port 39999
  User root
  IdentityFile ~/.ssh/id_ed25519

Host csa-old
  HostName 152.42.242.192
  User root
  # suspended — do not use
```

---

## 12. Rollback

1. Keep old VPS disk 7 days if provider allows.  
2. Re-enable `grok-farmer` on old host only if new host fails validation **and** old IP is reachable.  
3. Prefer not dual-running farmers (double inject risk); stop one first.  
4. Gateway is shared — never wipe `proxyPools` during farm rebuild.

---

## 13. Operator “go bag” (print / password manager)

- [ ] GitHub access to `fazulfi/grok-farm`  
- [ ] `age.identity` + `age.pubkey` on laptop  
- [ ] Latest `latest.tgz.age` pulled off-host  
- [ ] Gmail App Passwords (all identities)  
- [ ] `GROK_PASSWORD`  
- [ ] 9router SSH (port 39999)  
- [ ] Domain list + Pattern B map  
- [ ] S3 `backup.env` (recovered from 9router IDCloudHost keys or password manager)  
- [ ] This document + DEPLOYMENT.md  

---

## 14. Lessons from 2026-07-13 rebuild

1. **S3 keys only on dead disk** → first rebuild could not restore offsite; 9router export saved inventory. **Fixed:** re-provision `backup.env` from same is3.cloudhost.id account (shared with 9router backup) + hourly S3 age upload.  
2. **Root-owned logs** → auto inject silent-fail; always chown logs to farmer; use brutal **v5**.  
3. **Unquoted `GROK_PASSWORD` with `#`** → bash `source .env` truncates; quote secrets.  
4. **Chat-exposed secrets** → rotate App Passwords / root password after emergency paste.  
5. **Soft expired JWTs** after rebuild are normal; do not mass `--include-injected` error mark (keeps gateway IDs).  
6. Prefer S3 `latest.tgz.age` as offsite SoT; still keep laptop `age.identity` + optional local pull for PATH A offline.
