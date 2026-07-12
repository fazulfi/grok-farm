# Deployment & Migration Guide

**Goal:** Rebuild a dead Farm VPS from GitHub in under 30 minutes.

---

## 1. Prerequisites

| Item | Notes |
|------|--------|
| Ubuntu 22.04/24.04 VPS | 4 vCPU / 8 GB RAM recommended |
| Non-root sudo user | e.g. `magadirxwin` — **never run Camoufox as root** |
| Domain catch-all | Cloudflare Email Routing → Gmail |
| Gmail App Password | IMAP OTP |
| Residential proxies | Prefer non-EU for Grok 4.5 |
| Optional 9router | Gateway for inject + proxyPools |

---

## 2. Fresh install (from GitHub)

```bash
# As root: create user if needed
adduser --disabled-password --gecos "" magadirxwin
usermod -aG sudo magadirxwin
echo "magadirxwin ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/magadirxwin

# As farmer user
sudo -u magadirxwin -i
cd ~
git clone https://github.com/fazulfi/grok-farm.git grok-farm
cd grok-farm
chmod +x install.sh run.sh brutal_farmer.sh scripts/*.sh ops/*.py 2>/dev/null || true
./install.sh

cp .env.example .env
nano .env   # IMAP, domain, password, headless=true on VPS
```

### Swap (recommended)

```bash
sudo fallocate -l 8G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
echo 'vm.swappiness=10' | sudo tee -a /etc/sysctl.conf
sudo sysctl vm.swappiness=10
```

### Systemd

```bash
# Edit User/paths if your username differs
sudo cp systemd/grok-farmer.service /etc/systemd/system/
sudo sed -i "s/magadirxwin/$USER/g" /etc/systemd/system/grok-farmer.service
sudo systemctl daemon-reload
sudo systemctl enable --now grok-farmer
systemctl status grok-farmer --no-pager
```

### One-shot test (before enabling loop)

```bash
source .venv/bin/activate
python farm.py -n 1 -c 1 -y
```

---

## 3. 9router integration (optional but production)

On **gateway** host:

```bash
# From repo
sudo cp ops/list_proxies.py /root/list_proxies.py
sudo cp ops/grok_cli_bulk_inject.py /root/grok_cli_bulk_inject.py
sudo chmod +x /root/list_proxies.py /root/grok_cli_bulk_inject.py
```

On **farm** host: SSH key to gateway

```bash
ssh-keygen -t ed25519 -N "" -f ~/.ssh/id_ed25519
ssh-copy-id -p 39999 root@GATEWAY_IP   # or append pubkey to authorized_keys
ssh -p 39999 root@GATEWAY_IP 'echo OK'
```

Configure proxy pools in 9router UI/DB (non-EU).  
Workflow will sync pools automatically each loop.

Edit `workflow.py` / `sync_proxies_from_9r.py` host/port if gateway address differs.

---

## 4. Migration from dead VPS

### A. What you need offline

| Asset | Location |
|-------|----------|
| `.env` | secrets — password manager / encrypted backup |
| `akun.db` | account inventory |
| `results/` | optional historical batches |
| SSH key to 9router | if inject still required |
| This git repo | source of scripts |

### B. Restore steps

```bash
# 1) Fresh clone + install (section 2)
# 2) Restore secrets & DB
scp user@backup:grok-secrets/.env ~/grok-farm/.env
scp user@backup:grok-secrets/akun.db ~/grok-farm/akun.db
chmod 600 ~/grok-farm/.env ~/grok-farm/akun.db

# 3) Optional results
rsync -avz user@backup:~/grok-farm/results/ ~/grok-farm/results/

# 4) Re-link 9router SSH
# 5) Start service
sudo systemctl enable --now grok-farmer

# 6) Drain backlog inject
cd ~/grok-farm && source .venv/bin/activate
python3 import_db.py
python3 workflow.py
python3 check_status.py
```

### C. DNS / email

Catch-all domain unchanged if only VPS dies — no DNS change required.

---

## 5. Deploy script helpers

```bash
# From a workstation with gh + ssh
./scripts/deploy_farm_vps.sh user@farm-vps
./scripts/backup_farm.sh user@farm-vps ./backups
```

See scripts for flags.

---

## 6. Post-deploy checklist

- [ ] `systemctl is-active grok-farmer` → active  
- [ ] `free -h` shows swap  
- [ ] Not running as root  
- [ ] `python3 sync_proxies_from_9r.py` → count > 0 (if 9router)  
- [ ] One account appears in `results/batch_*`  
- [ ] `check_status.py` shows injected growth  
- [ ] Grok 4.5 works via 9router with proxy  

---

## 7. Version pin

Deploy from a release tag when possible:

```bash
git clone --branch v2.0.0 https://github.com/fazulfi/grok-farm.git
```
