# Operations Guide — Grok Farm

**Audience:** operators / on-call  
**Environment:** Linux VPS (Ubuntu 24.04+), systemd, non-root farmer user

---

## 1. Service inventory

| Service | Host | Unit / process | Purpose |
|---------|------|----------------|---------|
| `grok-farmer` | Farm VPS | `systemd` | Unlimited farm → import → inject loop |
| Camoufox | Farm VPS | child of `farm.py` | Browser automation |
| 9router | Gateway VPS | `next-server` / custom | LLM gateway + proxyPools |
| SSH tunnel path | Farm → Gateway | OpenSSH key | Proxy sync + inject |

---

## 2. Daily operations

### 2.1 Health checks

```bash
# Farm VPS
systemctl is-active grok-farmer
systemctl status grok-farmer --no-pager
free -h
swapon --show
tail -50 ~/grok-farm/farm_brutal.log
tail -50 ~/grok-farm/workflow.log
python3 ~/grok-farm/check_status.py

# Gateway VPS
curl -s http://127.0.0.1:20128/api/health
sqlite3 /var/lib/9router/db/data.sqlite \
  "SELECT COUNT(*) FROM proxyPools;
   SELECT COUNT(*) FROM providerConnections WHERE provider='grok-cli';
   SELECT COUNT(*) FROM providerConnections WHERE provider='xai';"
```

### 2.2 Expected steady state

- `grok-farmer`: **active (running)**
- Concurrent Camoufox parents ≈ `CONCURRENT` (e.g. 3)
- `workflow_pools` / `usa_proxies.txt` line count ≈ proxyPools count
- `farmed` backlog near **0** after each successful inject cycle
- Swap present if concurrent ≥ 3

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
```

---

## 4. Configuration

### 4.1 Farmer knobs

| File | Keys |
|------|------|
| `~/grok-farm/.env` | IMAP, domain, password, headless, proxy file path |
| `brutal_farmer.sh` | `ACCOUNTS_PER_BATCH`, `CONCURRENT`, `BATCH_DELAY` |
| `systemd` unit | `User=`, `WorkingDirectory=`, `Restart=` |

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

Do **not** hand-edit `usa_proxies.txt` for production; it is overwritten by sync.

---

## 5. Account inventory

```bash
# Summary
python3 ~/grok-farm/check_status.py

# SQL
sqlite3 ~/grok-farm/akun.db \
  "SELECT status, COUNT(*) FROM accounts GROUP BY status;"

# Export credentials (operator only)
sqlite3 -csv ~/grok-farm/akun.db \
  "SELECT email,password,status,batch_id FROM accounts;"
```

Statuses:

| Status | Meaning |
|--------|---------|
| `farmed` | Tokens in DB, not yet on 9router |
| `injected` | Present on 9router (grok-cli path) |

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
```

Retention: operator policy (disk is cheap; tokens are sensitive — encrypt backups).

---

## 7. Backup & restore

### Backup

```bash
# Farm
tar czf grok-farm-backup-$(date +%F).tgz \
  --exclude=.venv --exclude=screenshots \
  -C ~ grok-farm/.env grok-farm/akun.db grok-farm/results grok-farm/brutal_farmer.sh \
  grok-farm/workflow.py grok-farm/sync_proxies_from_9r.py grok-farm/import_db.py

# 9router (gateway)
sqlite3 /var/lib/9router/db/data.sqlite ".backup /tmp/9r.sqlite"
```

### Restore farmer DB

```bash
systemctl stop grok-farmer
cp akun.db akun.db.bak
cp /path/to/akun.db ~/grok-farm/akun.db
chown magadirxwin:magadirxwin ~/grok-farm/akun.db
systemctl start grok-farmer
```

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

---

## 10. Escalation

| Symptom | First action | Doc |
|---------|--------------|-----|
| Service dead | `systemctl status` + journal | [RUNBOOK.md](./RUNBOOK.md) |
| 0 new accounts | farm.log Turnstile/IMAP | RUNBOOK |
| Inject backlog | `workflow.py` manual + SSH | [INTEGRATION-9ROUTER.md](./INTEGRATION-9ROUTER.md) |
| Grok 4.5 region 403 | proxy pool non-EU | RUNBOOK geo |
