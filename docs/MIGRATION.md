# Migration Playbook — VPS Death / Rebuild

**RTO target:** < 30 minutes (with backup of `.env` + `akun.db`)  
**RPO target:** last successful `workflow.py` / batch write

---

## 1. Critical assets checklist

| Asset | Criticality | Where |
|-------|-------------|--------|
| Git repo | Code | GitHub `fazulfi/grok-farm` |
| `.env` | **SEV0 secret** | Encrypted backup only |
| `akun.db` | **SEV0 inventory** | Encrypted backup |
| Proxy pools | High | 9router DB (independent of farm VPS) |
| Domain catch-all | High | Cloudflare (independent) |
| Gmail IMAP | High | Google account |
| SSH key farm→9router | High | Operator keystore |

**If farm VPS dies but 9router + domain + Gmail survive:** only farmer host needs rebuild.

---

## 2. Cold start (no old disk)

1. Provision Ubuntu 24.04, 4c/8GB  
2. Create non-root user  
3. `git clone https://github.com/fazulfi/grok-farm.git`  
4. `./install.sh`  
5. Restore `.env` + `akun.db` from encrypted backup  
6. 8G swap  
7. Install systemd unit  
8. Restore SSH to 9router  
9. `python3 sync_proxies_from_9r.py && python3 workflow.py`  
10. `systemctl enable --now grok-farmer`  

Full commands: [DEPLOYMENT.md](./DEPLOYMENT.md).

---

## 3. Warm migration (old VPS still SSH-able)

```bash
# Workstation
./scripts/backup_farm.sh magadirxwin@OLD_IP ./backups
./scripts/deploy_farm_vps.sh magadirxwin@NEW_IP
./scripts/restore_farm.sh magadirxwin@NEW_IP ./backups/grok-farm-backup-XXXX.tgz

ssh magadirxwin@NEW_IP 'cd ~/grok-farm && python3 workflow.py && sudo systemctl enable --now grok-farmer'
ssh magadirxwin@OLD_IP 'sudo systemctl disable --now grok-farmer'
```

---

## 4. DNS / email

No change if domain catch-all already points to Gmail.

---

## 5. Validation gate

| Check | Command / UI |
|-------|----------------|
| Service up | `systemctl is-active grok-farmer` |
| Proxy sync | `python3 sync_proxies_from_9r.py` |
| Inject path | `python3 workflow.py` |
| Dashboard | 9router → Grok CLI connections |
| Model | Grok 4.5 via non-EU proxy |

---

## 6. Rollback

Keep old VPS powered off but disk retained 7 days.  
Re-enable `grok-farmer` on old host if new host fails validation.
