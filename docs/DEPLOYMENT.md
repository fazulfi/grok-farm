# Deployment & Migration Guide

**Goal:** Install or rebuild a Farm VPS to production parity (farm → import → inject → backup).  
**Companion DR playbook:** [MIGRATION.md](./MIGRATION.md) (PATH A/B/C/D, proven 2026-07-13).  
**Version pin:** prefer git tag `v2.2.0` or newer (`main`).

---

## 1. Prerequisites

| Item | Notes |
|------|--------|
| Ubuntu 22.04/24.04 VPS | **4 vCPU / 8 GB RAM** recommended; non-EU egress preferred for Grok 4.5 |
| Non-root sudo user | e.g. `magadirxwin` — **never run Camoufox as root** |
| Domain catch-all (1..N) | Cloudflare Email Routing → Gmail (Pattern B = 1 domain → 1 Gmail) |
| Gmail App Password(s) | IMAP OTP; store in password manager |
| Residential proxies | Via 9router **proxyPools** (non-EU) |
| 9router gateway | SSH inject + proxyPools (production) |
| Optional age keys | Encrypt backups; laptop holds `age.identity` |
| Optional S3 | `is3.cloudhost.id` bucket `grok-farm` via `backup.env` |

---

## 2. Fresh install (from GitHub)

### 2.1 Root bootstrap

```bash
export FARM_USER=magadirxwin
adduser --disabled-password --gecos "" "$FARM_USER"
usermod -aG sudo "$FARM_USER"

# LEAST PRIVILEGE — do NOT use NOPASSWD:ALL
cat >/etc/sudoers.d/grok-farmer-$FARM_USER <<EOF
$FARM_USER ALL=(root) NOPASSWD: /bin/systemctl start grok-farmer, /bin/systemctl stop grok-farmer, /bin/systemctl restart grok-farmer, /bin/systemctl status grok-farmer, /bin/systemctl is-active grok-farmer, /bin/systemctl reload grok-farmer, /bin/journalctl -u grok-farmer *
EOF
chmod 440 /etc/sudoers.d/grok-farmer-$FARM_USER

apt-get update
apt-get install -y python3 python3-venv python3-pip git curl wget unzip \
  xvfb libgtk-3-0 libx11-xcb1 age rsync sqlite3

# 8G swap (Camoufox concurrent)
fallocate -l 8G /swapfile
chmod 600 /swapfile
mkswap /swapfile
swapon /swapfile
grep -q swapfile /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
echo 'vm.swappiness=10' >> /etc/sysctl.conf
sysctl vm.swappiness=10
```

### 2.2 Code + install as farmer

```bash
sudo -u "$FARM_USER" -i
cd ~
git clone --branch v2.2.0 https://github.com/fazulfi/grok-farm.git grok-farm
# or main for latest
cd grok-farm
chmod +x install.sh run.sh brutal_farmer.sh scripts/*.sh
./install.sh
# Creates .venv, installs Camoufox browser + GeoIP + uBlock — long running
```

Workstation helper:

```bash
./scripts/deploy_farm_vps.sh magadirxwin@FARM_IP --branch main
```

### 2.3 Secrets

```bash
cp .env.example .env
chmod 600 .env
nano .env
```

**Minimum production `.env` keys:**

| Key | Example / note |
|-----|----------------|
| `GROK_IMAP_USER` | primary Gmail |
| `GROK_IMAP_PASS` | App Password (spaces OK; strip if needed) |
| `GROK_EMAIL_MODE` | `domain` |
| `GROK_EMAIL_DOMAINS` | `budgezen.com,mypapyr.com` |
| `GROK_EMAIL_DOMAIN_STRATEGY` | `round_robin` |
| `GROK_PASSWORD` | **Single-quoted** if contains `#` `&` `!` |
| `GROK_HEADLESS` | `true` on VPS |
| `GROK_CONCURRENT` | `3` |
| `GROK_CONCURRENT_MIN` / `MAX` | `1` / `3` |
| `GROK_ADAPTIVE_CONCURRENT` | `1` |
| `GROK_PROXY_FILE` | `/home/magadirxwin/grok-farm/usa_proxies.txt` |
| `GROK_9R_SSH` | `root@49.12.82.34` |
| `GROK_9R_PORT` | `39999` |
| `GROK_9R_KEY` | `/home/magadirxwin/.ssh/id_ed25519` |
| `GROK_MID_DRAIN_INTERVAL` | `120` (brutal v5 mid-batch inject) |

Multi-Gmail Pattern B:

```bash
cp identities.example.json identities.json
chmod 600 identities.json
# edit: primary domains=[budgezen.com], secondary domains=[mypapyr.com] enabled=true
```

See RUNBOOK **R11** and MIGRATION §5 B.4.

### 2.4 Logs must be farmer-owned

```bash
touch farm_brutal.log workflow.log
chown "$USER:$USER" farm_brutal.log workflow.log
# If ever root-owned → Permission denied on auto inject (brutal v5 documents fallback)
```

### 2.5 Systemd

```bash
# As root — substitute user/home if different
sed "s/magadirxwin/USER/g; s|/home/magadirxwin|/home/USER|g" \
  systemd/grok-farmer.service > /etc/systemd/system/grok-farmer.service
sed "s/magadirxwin/USER/g; s|/home/magadirxwin|/home/USER|g" \
  systemd/grok-farm-backup.service > /etc/systemd/system/grok-farm-backup.service
cp systemd/grok-farm-backup.timer /etc/systemd/system/
systemctl daemon-reload
# Do NOT start farmer until .env + proxies ready
```

### 2.6 One-shot test (before unlimited loop)

```bash
source .venv/bin/activate
python farm.py -n 1 -c 1 -y
# Expect OTP + account line in results/batch_*/
python3 import_db.py
python3 workflow.py   # needs 9router SSH + helpers
```

---

## 3. 9router integration (production)

### 3.1 Gateway host (once)

```bash
# From repo on gateway as root
cp ops/list_proxies.py /root/list_proxies.py
cp ops/grok_cli_bulk_inject.py /root/grok_cli_bulk_inject.py
cp ops/list_grok_connections.py /root/list_grok_connections.py
cp ops/export_9r_to_akun.py /root/export_9r_to_akun.py
chmod +x /root/list_proxies.py /root/grok_cli_bulk_inject.py \
         /root/list_grok_connections.py /root/export_9r_to_akun.py
```

DB path used by helpers: `/var/lib/9router/db/data.sqlite`.

### 3.2 Farm → gateway SSH

```bash
# As farmer user
ssh-keygen -t ed25519 -N "" -f ~/.ssh/id_ed25519
# Append pubkey to gateway root authorized_keys
ssh -p 39999 -i ~/.ssh/id_ed25519 -o BatchMode=yes root@GATEWAY_IP 'echo OK'
```

### 3.3 Proxy pools

Configure non-EU residential pools in 9router UI/DB. Then:

```bash
python3 sync_proxies_from_9r.py
wc -l usa_proxies.txt   # expect > 0
```

**Source of truth = proxyPools**, not a hand-edited list.

### 3.4 Inject contract

- Providers: `grok-cli` + `xai`
- Workflow: SSH + `/root/grok_cli_bulk_inject.py` JSONL
- Empty proxy pool → abort inject (fail-closed)
- Details: [INTEGRATION-9ROUTER.md](./INTEGRATION-9ROUTER.md)

---

## 4. Migration from dead VPS

| Path | When | Doc |
|------|------|-----|
| **A** | Have `latest.tgz.age` + `age.identity` | MIGRATION §4 |
| **B** | No backup; 9router has tokens | MIGRATION §5 + `ops/export_9r_to_akun.py` |
| **C** | Old host still SSH | MIGRATION §6 + `backup_farm.sh` / `restore_farm.sh` |
| **D** | Empty cold start | MIGRATION §7 |

Quick PATH B one-liner on gateway:

```bash
python3 /root/export_9r_to_akun.py /tmp/akun_rebuild.db
# scp to farm → akun.db chmod 600; re-enter .env + identities; start farmer
```

---

## 5. Deploy script helpers

```bash
# Workstation with git + ssh
./scripts/deploy_farm_vps.sh magadirxwin@farm-vps --branch main
./scripts/backup_farm.sh magadirxwin@farm-vps ./backups
./scripts/restore_farm.sh magadirxwin@farm-vps ./backups/xxx.tgz
# Local age (on farm)
bash scripts/local_age_backup.sh
# S3 age (needs backup.env)
bash scripts/s3_backup.sh
```

---

## 6. Backup units

| Mode | Trigger | Output |
|------|---------|--------|
| Local age | `grok-farm-backup.timer` hourly | `~/grok-farm/backups/*.tgz.age` |
| S3 age | same script if `~/.config/grok-farm/backup.env` exists | `s3://grok-farm/farm-vps/<host>/` |

Pubkey: `~/.config/grok-farm/age.pubkey`  
Identity (decrypt): laptop + optional `/root/.config/grok-farm/age.identity` (600).

---

## 7. Post-deploy checklist

- [ ] `systemctl is-active grok-farmer` → **active**
- [ ] `free -h` / swap present
- [ ] Not running as root
- [ ] `.env` `identities.json` `akun.db` mode **600**
- [ ] `workflow.log` / `farm_brutal.log` owned by farmer
- [ ] `grep 'Brutal Farmer v5' farm_brutal.log`
- [ ] `python3 sync_proxies_from_9r.py` → count > 0
- [ ] `python3 check_status.py` — identity pool, proxies, backup status
- [ ] Mid-drain or post-batch `SUMMARY N ok 0 fail`
- [ ] 9router grok-cli connections grow
- [ ] Grok model via non-EU proxy on gateway
- [ ] No `NOPASSWD:ALL` for farmer user

---

## 8. Version pin

```bash
git clone --branch v2.2.0 https://github.com/fazulfi/grok-farm.git
# After pull of hotfixes (brutal v5, etc.):
git fetch --tags && git checkout main   # or newer tag when released
```

Current production host reference: **168.144.137.240** / user **magadirxwin** / gateway **49.12.82.34:39999** (update this line when infrastructure moves).

---

## 9. Security notes for deploy

1. Never commit `.env`, `identities.json`, `akun.db`, keys, `backup.env`.  
2. Do not echo App Passwords / JWT into chat or ticket systems.  
3. After emergency paste of secrets into chat → **rotate**.  
4. Prefer key-only root SSH; change root password if it was chat-exposed.  
5. See [SECURITY.md](./SECURITY.md).
