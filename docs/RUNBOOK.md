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

1. Cloudflare Email Routing catch-all → Gmail **Active** (check **each** domain in `GROK_EMAIL_DOMAINS`)
2. Gmail App Password valid (no spaces issues in `.env` / `identities.json`)
3. Spam folder
4. Test: send mail to `random@each-domain` and confirm IMAP sees it
5. `python3 check_status.py` — identity pool lists expected domains
6. If multi-IMAP: confirm `imap_for_email` maps domain to correct inbox
7. Increase OTP timeout in `.env` if present (`GROK_OTP_TIMEOUT`)
8. Farm log timeout now prints `scanned_xai=` / `near_domain_misses=` — if `scanned_xai>0` but no match, catch-all headers may strip alias; if no mail for domain in IMAP at all, **routing is broken**

### R3b — New domain not used in farm

1. Confirm `GROK_EMAIL_DOMAINS` includes domain (comma, no spaces issues)
2. Confirm catch-all Active
3. Wait for **next** farm.py process (systemd loop reloads env each batch)
4. Check `batch_*/batch_meta.json` → `email_domains`

### R3c — Domain selected but OTP never for that domain (multi-domain)

**Symptom:** farm starts `user@newdomain.com`, IMAP waits, timeout; budgezen (or known-good domain) still gets OTP.

**Evidence probe (on farmer host, secrets not printed):**

```bash
# After a failed @newdomain attempt, confirm zero mail To: that domain in Gmail
python3 - <<'PY'
import imaplib, os
from pathlib import Path
env={}
for line in Path(".env").read_text().splitlines():
    if not line.strip() or line.startswith("#") or "=" not in line: continue
    k,v=line.split("=",1); env[k.strip()]=v.strip().strip('"').strip("'")
m=imaplib.IMAP4_SSL(env.get("GROK_IMAP_HOST","imap.gmail.com"),993)
m.login(env["GROK_IMAP_USER"], env["GROK_IMAP_PASS"].replace(" ",""))
m.select("INBOX")
st,msgs=m.search(None,'(FROM "x.ai")')
ids=msgs[0].split()[-20:]
for mid in ids:
    st,data=m.fetch(mid,'(BODY.PEEK[HEADER.FIELDS (SUBJECT TO)])')
    print(data[0][1].decode("utf-8","replace").replace("\n"," | ")[:200])
m.logout()
PY
```

| Finding | Meaning | Action |
|---------|---------|--------|
| No `To: *@newdomain` in recent xAI mail | Catch-all / MX / routing destination wrong | Fix Cloudflare Email Routing destination = same Gmail as `GROK_IMAP_USER`; wait DNS; retest with external mail to `probe@newdomain` |
| Mail arrives but farm still times out | Matcher miss | Upgrade `farm.py` IMAP headers (X-Original-To, Received, body); check spam |
| Only one domain works | Temporarily set `GROK_EMAIL_DOMAINS=good.domain` only | Avoid burning concurrent slots on dead domain until R3c green |
| `domain_stats` shows high `consec` / DISABLED | Auto-skip engaged after N OTP/farm fails | Fix routing, then `UPDATE domain_stats SET consecutive_fails=0, disabled=0 WHERE domain='…'` or wait for a success |

### R3d — Domain health auto-skip (`domain_stats`)

**Symptoms:** multi-domain pool configured but bad domain no longer selected; `check_status.py` shows `domain_stats:` with high `consec`.

| Step | Action |
|------|--------|
| 1 | `python3 check_status.py` — inspect `domain_stats` lines |
| 2 | Threshold: `GROK_DOMAIN_MAX_CONSECUTIVE_FAILS` (default 3) |
| 3 | After Cloudflare catch-all fixed: clear stats for that domain or re-add only after IMAP probe green |
| 4 | Optional: leave dead domain out of `GROK_EMAIL_DOMAINS` until verified |

```sql
-- re-enable after routing fixed (farmer host, akun.db)
UPDATE domain_stats SET consecutive_fails=0, disabled=0, updated_at=datetime('now')
 WHERE domain='mypapyr.com';
```

**Do not** assume Cloudflare UI “Active” means OTP inbox receives xAI mail — always verify with IMAP probe.

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

## R10 — Expired / invalid JWT inventory

**Symptoms:** `check_status.py` shows many `token_health=expired`; soft issue `many_expired_tokens`; inject backlog never drains because farmed rows are dead tokens; workflow marks `notes=token_expired`.

### Policy (enterprise)

| Row status | Dead JWT action |
|------------|-----------------|
| `farmed` | Always mark `status=error`, `notes=token_expired` or `bad_token` (import fail-closed + mark CLI) |
| `injected` | Refresh `token_exp` / `token_health` only by default; optional `--include-injected` sets `status=error` for inventory hygiene |
| Gateway | **No auto-delete** of 9router `providerConnections` — re-farm or re-auth is a separate operator decision |

### Commands

```bash
cd ~/grok-farm
source .venv/bin/activate

# Preview
python3 mark_expired_tokens.py --dry-run
python3 mark_expired_tokens.py --dry-run --json

# Apply farmed-only (safe default)
python3 mark_expired_tokens.py
# or: python3 check_status.py --mark-expired

# Optional: also mark injected expired as error (inventory only)
python3 mark_expired_tokens.py --include-injected
# or: python3 check_status.py --mark-expired-injected
```

### After cleanup

1. `python3 check_status.py` — `farmed` should not hold expired tokens; hard issue `farmed_expired_tokens` cleared
2. Soft `many_expired_tokens` may remain if injected JWTs aged out and you did **not** use `--include-injected`
3. To restore capacity: farm fresh accounts (realistic local-parts) → import → workflow inject
4. Optionally prune dead gateway connections on 9router (manual SQL / UI) — out of band from this CLI

### Do not

- Re-inject known-expired farmed rows hoping they revive
- Commit tokens or dump JWT payloads into tickets
- Treat soft expired inventory as SEV1 unless gateway yield is actually broken

---

## Severity matrix

| Sev | Example | Response time |
|-----|---------|---------------|
| SEV1 | All farming stopped + inject broken | Immediate |
| SEV2 | High fail rate (>50%) | < 4h |
| SEV3 | Single proxy bad | Next business day |
| SEV4 | Docs/log noise | Backlog |
