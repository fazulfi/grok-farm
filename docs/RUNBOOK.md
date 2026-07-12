# Incident Runbook — Grok Farm

---

## R1 — `grok-farmer` not running

**Symptoms:** `systemctl is-active` ≠ active; no new batches.

```bash
systemctl status grok-farmer --no-pager
journalctl -u grok-farmer -n 100 --no-pager
```

**Actions:**

1. `systemctl restart grok-farmer`
2. If crash loop: run manually  
   `su - magadirxwin -c 'cd ~/grok-farm && bash -x brutal_farmer.sh'`
3. Check disk: `df -h`
4. Check permissions on `~/grok-farm` (owner = farmer user)

---

## R2 — Farm creates 0 accounts

**Symptoms:** batches empty / all FAIL in `farm.log`.

| Cause | Check | Fix |
|-------|-------|-----|
| Turnstile | screenshots `*_after_signup` | Better proxies; lower concurrent |
| No email input | UI change | Update farm.py selectors |
| OTP timeout | IMAP logs | App password; catch-all routing |
| Root user | error message | Run as non-root |
| Proxy dead | sync count 0 | Fix 9router pools |

```bash
tail -100 results/batch_*/farm.log | tail -50
ls -lt screenshots | head
python3 sync_proxies_from_9r.py
```

---

## R3 — OTP never arrives

1. Cloudflare Email Routing catch-all → Gmail **Active**
2. Gmail App Password valid (no spaces issues in `.env`)
3. Spam folder
4. Test: send mail to `random@yourdomain` and confirm IMAP sees it
5. Increase OTP timeout in `.env` if present

---

## R4 — Inject backlog (`farmed` >> 0)

```bash
python3 import_db.py
python3 workflow.py
# expect SUMMARY N ok 0 fail
python3 check_status.py
```

| Cause | Fix |
|-------|-----|
| SSH key broken | test `ssh -i ~/.ssh/id_ed25519 root@GW -p 39999 echo ok` |
| Injector missing | redeploy `/root/grok_cli_bulk_inject.py` |
| 9router down | start gateway; `curl localhost:20128/api/health` |
| Empty proxy pools | refill proxyPools; re-run sync |

---

## R5 — Grok 4.5 `not available in your region`

**Cause:** Egress IP is EU (or blocked region).

**Fix:**

1. Ensure connection has `connectionProxyEnabled=true`
2. Proxy from non-EU pool (US/SG/etc.)
3. Dashboard: re-test connection after proxy assign
4. Bulk re-apply proxies: `update_9r_proxies.py` / re-run workflow upsert

---

## R6 — OOM / browser kills

```bash
free -h
swapon --show
dmesg | grep -i 'killed process' | tail
```

**Fix:**

1. Ensure 8G swap (or more)
2. Lower `CONCURRENT` to 2
3. `vm.swappiness=10` already recommended

---

## R7 — XPCOM / Camoufox crash

**Message:** incomplete camoufox under root.

**Fix:** never root; reinstall camoufox as farmer user (`bash install.sh` / copy cache ownership).

---

## R8 — Duplicate emails

Dedup files: `results/used_emails.txt` + DB unique email.

If collision: check used_emails permissions; scan batches.

---

## R9 — 9router dashboard empty for Grok CLI

Tokens may be under **xai** only if old inject path used.

**Fix:** run `workflow.py` / `grok_cli_bulk_inject.py` path that writes `provider='grok-cli'`.

```bash
sqlite3 /var/lib/9router/db/data.sqlite \
  "SELECT COUNT(*) FROM providerConnections WHERE provider='grok-cli';"
```

Hard refresh browser dashboard.

---

## Severity matrix

| Sev | Example | Response time |
|-----|---------|---------------|
| SEV1 | All farming stopped + inject broken | Immediate |
| SEV2 | High fail rate (>50%) | < 4h |
| SEV3 | Single proxy bad | Next business day |
| SEV4 | Docs/log noise | Backlog |
