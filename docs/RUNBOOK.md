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
| `injected` | **Enterprise default = soft only**: refresh `token_exp` / `token_health`; do **not** set `status=error` (preserves 9router `grok_cli_connection_id` / inject marks). Optional `--include-injected` hard-marks inventory only — still **no** gateway revoke |
| Gateway | **No auto-delete** of 9router `providerConnections`. Farm product does **not** re-auth; capacity recovery = **re-farm + re-inject** (consumer/9router owns post-inject validity) |

**Recommended default:** run `mark_expired_tokens.py` without `--include-injected`. Soft health issue `many_expired_tokens` is inventory noise, not SEV1. Soft meta ≠ session management.


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
3. To restore capacity: **re-farm** fresh accounts → import → workflow inject (not browser re-auth on old rows)
4. Optionally prune dead gateway connections on 9router (manual SQL / UI) — out of band from this CLI; farm is not a session manager

### Do not

- Re-inject known-expired farmed rows hoping they revive
- Commit tokens or dump JWT payloads into tickets
- Treat soft expired inventory as SEV1 unless gateway yield is actually broken
- Default to `--include-injected` in cron/systemd (breaks inventory↔gateway linkage for ops)

---

## R11 — Multi-Gmail / second inbox (Pattern B)

**Goal:** split catch-all domains across two Gmail App Password inboxes via `identities.json`.

### Cloudflare (required per domain zone)

1. **Email Routing → Destination addresses:** add **each** Gmail (primary + secondary); verify both.
2. **Catch-all / routing rules:** for domain assigned to secondary, route `*@domain` → secondary Gmail (not only primary).
3. Do **not** enable secondary identity in farm until a probe address receives mail in the **secondary** IMAP inbox.

### Farm config (VPS only — never git)

```bash
# ~/grok-farm/identities.json  chmod 600  owner farmer
# Pattern B example:
# primary  → domain-a.com  (imap primary@gmail.com)
# secondary → domain-b.com (imap secondary@gmail.com)  enabled after CF green
```

```bash
python3 -c "from email_identity import load_identity_pool; import json; print(json.dumps(load_identity_pool('.').summary(), indent=2))"
# expect identity_count=2, source=file:.../identities.json
python3 check_status.py   # identity ids=2
```

### Safe rollout

1. Deploy `identities.json` with secondary **`enabled: false`** (primary still owns both domains).
2. CF destination verify + catch-all for domain-b → secondary Gmail.
3. IMAP probe: send to `probe@domain-b` → confirm in secondary inbox.
4. Edit JSON: secondary `enabled: true`, remove domain-b from primary `domains`, keep `GROK_EMAIL_DOMAINS=a,b`.
5. Live-safe: `pkill -f 'python.*farm.py'` (brutal loop respawns; do **not** stop systemd unless needed).
6. Clear `domain_stats` for domain-b if it was skipped (R3d).

### Rollback

- Delete or rename `identities.json` → pool falls back to single `GROK_IMAP_*` env identity.
- Or set secondary `enabled: false` and restore domain on primary.

---

## R12 — S3 retention / age decrypt (operator laptop)

**Retention:** hourly backup runs `s3_upload.py --retention` (boto3). Keeps `latest.tgz.age` + `LATEST.txt`; deletes dated objects older than `RETENTION_DAYS` (default 14) under `S3_PREFIX/HOST/`.

```bash
# Manual retention (as farmer, with backup.env sourced)
set -a; source ~/.config/grok-farm/backup.env; set +a
export HOST_NAME="$(hostname -s)"
python3 ~/grok-farm/scripts/s3_upload.py --retention --dry-run
python3 ~/grok-farm/scripts/s3_upload.py --retention
```

**Windows age CLI (optional):**

```powershell
# Scoop:  scoop install age
# Chocolatey:  choco install age.portable
# Then decrypt:
age -d -i $env:USERPROFILE\.config\grok-farm\age.identity -o restore.tgz backup.tgz.age
```

If age is not on PATH, decrypt on Linux/VPS that has the vaulted identity.

---

## R13 — Live token probe / `needs_relogin` inventory

**Goal:** Optional **observability** — sample which injected JWTs still work
against xAI. This is **not** session recovery and **not** a re-auth workflow.
Farm product scope remains autofarm + auto-inject only.

**Endpoint:** `GET https://api.x.ai/v1/models` with
`Authorization: Bearer <access_token>` (stdlib only).

**Default policy is soft:** write `last_probe_*` + `needs_relogin` meta only.
Do **not** pass `--mark-error` unless you intentionally hard-mark inventory
(still no reauth; capacity recovery = re-farm + inject).
### Large-batch soft probe (P0)

Use a capped soft run against injected inventory. Selection order is stable
(`ORDER BY id ASC`). Re-running the same `--limit` re-probes the same head of
the queue (raise limit when you need a wider sample).

```bash
cd ~/grok-farm
# Soft large batch (default: NO --mark-error)
python3 probe_tokens.py --limit 200
python3 probe_tokens.py --limit 200 --json

# Health surface after probe (soft_policy / inventory dashboard)
python3 check_status.py
# Expect: probe: needs_relogin=N last_status={alive:…, needs_relogin:…, …}

# Gateway vs farm diff (no tokens)
python3 reconcile_9router.py --json
```

Smaller samples / dry-run:

```bash
python3 probe_tokens.py --limit 50
python3 probe_tokens.py --json --limit 20
python3 probe_tokens.py --dry-run --limit 5   # no DB writes

# Via health CLI (sample probe, soft mark_error=False)
python3 check_status.py --probe --probe-limit 20
python3 check_status.py --json | jq '.probe, .probe_run, .issues'

# Optional hard mark (rare): status=error notes=needs_relogin
python3 probe_tokens.py --mark-error --limit 10
```

| Flag / env | Default | Effect |
| --- | --- | --- |
| `--dry-run` | off | Probe only, no meta write |
| `--limit N` | all matching | Cap work (use 200 for large soft batch) |
| `--include-farmed` | off | Also probe `status=farmed` |
| `--no-skip-expired` | off | Force HTTP even if offline JWT expired |
| `--mark-error` | **off** | Hard `status=error` on `needs_relogin` |
| `GROK_PROBE_SKIP_EXPIRED` | `1` | Offline exp → `jwt_expired` (no HTTP) |
| `GROK_PROBE_TIMEOUT` | ~15s | HTTP timeout |
| `GROK_PROBE_LIMIT` | 20 | Default for `check_status --probe` |
| `GROK_PROBE_DELAY` | `0.15` | Reserved in `.env.example` (not wired yet) |

### Inventory interpretation

- **`needs_relogin`:** live HTTP rejected token. Soft issue (exit 2).
  Stays `injected`. No mass `--mark-error`.
- **`jwt_expired` (offline):** JWT `exp` past; skip HTTP if skip-expired.
  Age signal only, not ban. Not live relogin.
- **`alive`:** live probe OK. Clears `needs_relogin` when meta written.
- **`network_error` / rate / spend:** transient or quota.
  Does not clear `needs_relogin`; re-run later.

**Notes:**

- Offline `jwt_expired` ≠ ban; Discord FYI: soft inventory noise, not “coid”.
- Soft policy keeps gateway inject IDs; hard `--mark-error` only when operator
  accepts losing inject-queue semantics for those rows.
- Full browser re-auth / session refresh is **out of scope** (farm-only product).
  High `needs_relogin` is expected under soft policy and does **not** mean farm failure.
- After large soft probe: `python3 reconcile_9router.py --json`
  (bucket `needs_relogin_db`) — diff only, not recovery.

**Do not:** mass `--mark-error` on injected expecting reauth; farm does not manage sessions.
---

## R14 — Reconcile 9router vs `akun.db`

**Goal:** Diff gateway connections vs farm DB without printing tokens.

```bash
# Once: deploy list helper to gateway (same pattern as inject helpers)
scp -P 39999 ops/list_grok_connections.py root@49.12.82.34:/root/list_grok_connections.py

# On farm
python3 reconcile_9router.py
python3 reconcile_9router.py --json --limit-print 5
python3 reconcile_9router.py --write-notes              # soft notes gateway_missing
# farmed-only hard mark if missing on gateway:
python3 reconcile_9router.py --write-notes --mark-error
```

| Bucket | Meaning |
|--------|---------|
| `in_both_ok` | Email in DB + gateway, healthy enough |
| `in_db_not_gateway` | Farm has row; gateway missing |
| `in_gateway_not_db` | Gateway orphan (not in akun.db) |
| `jwt_expired_both` | Both sides show expired JWT meta |
| `needs_relogin_db` | Farm `needs_relogin=1` |
| `inactive_gateway` | Gateway `isActive` false |
| `duplicate_emails_gateway` | Dup emails on gateway |

Default = **dry report only**. Never deletes gateway rows.

See `docs/INTEGRATION-9ROUTER.md` §8.

---

## R15 — Adaptive concurrent (1–5)

**Goal:** Scale Camoufox workers per batch from live `domain_stats` + `proxy_stats` without editing systemd.

```bash
python3 adaptive_concurrent.py --print   # single int for shell
python3 adaptive_concurrent.py --json    # scores + decision

# brutal_farmer.sh (each batch):
# CONCURRENT=$(python3 adaptive_concurrent.py --print 2>/dev/null || echo "$CONCURRENT")
```

| Env | Default | Meaning |
|-----|---------|---------|
| `GROK_CONCURRENT` | 3 | Base / floor input |
| `GROK_ADAPTIVE_CONCURRENT` | `1` | `0` = fixed base only |
| `GROK_CONCURRENT_MIN` | 1 | Floor |
| `GROK_CONCURRENT_MAX` | 5 | Ceiling (RAM / Turnstile) |

Live-safe: next batch picks new concurrent; no `systemctl stop` required. Do not raise MAX without RAM/swap headroom (see CAPACITY).

---

## Severity matrix

| Sev | Example | Response time |
|-----|---------|---------------|
| SEV1 | All farming stopped + inject broken | Immediate |
| SEV2 | High fail rate (>50%) | < 4h |
| SEV3 | Single proxy bad | Next business day |
| SEV4 | Docs/log noise | Backlog |


---

## R16 — Farm VPS dead / rebuild

**Symptom:** SSH to farm host fails permanently; provider suspended; disk gone.

**Do not:** wipe 9router proxyPools or mass-delete providerConnections while rebuilding farm.

### Immediate triage

1. Confirm gateway still up: SSH `root@GATEWAY -p 39999`.
2. Confirm laptop has `age.identity` and/or password-manager secrets.
3. Choose path in **docs/MIGRATION.md**:
   - **PATH A** — decrypt `latest.tgz.age` → full restore
   - **PATH B** — `python3 /root/export_9r_to_akun.py /tmp/akun_rebuild.db` on 9router → rebuild inventory; re-enter `.env` + `identities.json`
   - **PATH C** — old host still briefly alive → `backup_farm.sh` then cutover
   - **PATH D** — empty cold start

### After new host is up

| Check | Pass |
|-------|------|
| `systemctl is-active grok-farmer` | active |
| Logs owned by farmer | not root (else inject Permission denied) |
| `Brutal Farmer v5` in farm_brutal.log | yes |
| `sync_proxies_from_9r.py` | lines > 0 |
| `check_status.py` | identity + proxies + backup |
| Mid-drain / SUMMARY | inject reaches 9router |

Full step-by-step: **docs/MIGRATION.md** + **docs/DEPLOYMENT.md**.

### Prevention

- Weekly: `scp csa:.../backups/latest.tgz.age` to laptop
- Keep Gmail App Passwords + `GROK_PASSWORD` in password manager
- Keep `age.identity` offline (laptop ACL locked)
- Optional: restore S3 `backup.env` for offsite age archives

---

## R17 — Health timer / hard vs soft exit codes

**Symptoms:** Discord/Telegram alert “health check hard issues”; `logs/health_check.log`
shows `exit_code=3`; soft noise (`exit_code=2`) floods logs but should **not** webhook;
timer failed / inactive.

### What the timer does

| Item | Value |
|------|--------|
| Units | `grok-farm-health.service` + `grok-farm-health.timer` |
| Schedule | every 15 min (`*:0/15`, RandomizedDelaySec ≤2m) |
| Script | `scripts/health_check.sh` |
| Check | `check_status.py --json` only — **no** `--probe`, **no** `--mark-error` |
| Exit 0 | healthy |
| Exit 2 | soft-only → **log only** (no webhook) |
| Exit 3 | hard issues → **webhook** if set (debounced) |
| SuccessExitStatus | `2 3` (oneshot unit stays green) |
| Ban risk | **zero** (inventory / local files / systemctl only) |

### Hard vs soft issue map

| Issue code | Severity | Follow |
|------------|----------|--------|
| `farmer_not_active` | **hard** | R1 / `systemctl status grok-farmer` |
| `backup_stale` / `backup_no_log` | **hard** | backup log + R12 / SECURITY |
| `disk_high` | **hard** | free disk; CAPACITY |
| `farmed_expired_tokens` | **hard** | **R10** (farmed dead JWT) |
| `large_farmed_backlog` | **hard** | run `workflow.py` / check 9router SSH |
| `proxy_file_empty` | **hard** | sync proxyPools; refill gateway |
| `identity_config_error` / `no_email_domains` | **hard** | `.env` / `identities.json` |
| `many_expired_tokens` | **soft** | **R10** inventory noise |
| `needs_relogin` | **soft** | **R13** (do not auto reauth) |
| `proxy_pool_low` | **soft** | refill pools; **R18** |
| `proxy_many_soft_skipped` | **soft** | **R18** |

### Triage

| Step | Action |
|------|--------|
| 1 | `systemctl status grok-farm-health.timer --no-pager` — should be active |
| 2 | `tail -80 ~/grok-farm/logs/health_check.log` — note exit_code + alert_sent |
| 3 | `cd ~/grok-farm && python3 check_status.py` — hard vs soft lists |
| 4 | Map issue → table above / existing runbook |

### Manual oneshot

```bash
sudo systemctl start grok-farm-health.service
# or as farmer:
cd ~/grok-farm && bash scripts/health_check.sh; echo exit=$?
# 0=ok  2=soft-only  3=hard
```

### Enable after deploy

```bash
# deploy_farm_vps.sh enables timer; or:
sudo systemctl enable --now grok-farm-health.timer
```

**Do not** stop `grok-farmer` to “fix” health noise. Soft exit 2 is expected under
high `needs_relogin` / JWT expired inventory — not a farm outage.

---

## R18 — Proxy soft-evict (score-weighted pick; no gateway DELETE)

**Symptoms:** inject fail rate high; `proxy_stats` many low scores / high
`consecutive_fails`; soft issue `proxy_many_soft_skipped` or `proxy_pool_low`;
farm skipped after empty local proxy file.

### Behavior (product)

| Item | Rule |
|------|------|
| Soft skip | `disabled=1` **or** `consecutive_fails >= GROK_PROXY_MAX_CONSECUTIVE_FAILS` (default **5**) |
| Pick | `workflow.pick_proxy()` score-weighted among non-skipped |
| Fail-open | if **all** proxies would be skipped → use full live list (never hard-stop inject on soft skip alone) |
| Empty live pool | still **fail-closed** (abort inject) — same as before |
| Empty local file | `brutal_farmer` skips **farm** that round after sync |
| Gateway DELETE | **never automatic** — manual/CLI only (`proxy_cleaner` ops) |

### Triage

```bash
cd ~/grok-farm
python3 check_status.py --json | jq '.proxy_soft_skip, .issues_soft, .issues_hard'
sqlite3 akun.db \
  "SELECT proxy_key, success_count, fail_count, consecutive_fails, disabled, score
   FROM proxy_stats ORDER BY consecutive_fails DESC, score ASC LIMIT 15;"
# Refill gateway proxyPools (non-EU), then:
python3 sync_proxies_from_9r.py
```

**Do not** mass-DELETE `proxyPools` from the farm product path. Clear soft-skip by
successful injects (resets consecutive_fails) or SQL `disabled=0` after fixing the
node.

---

## R19 — Soft probe / reconcile / mark-expired timers

**Symptoms:** timers inactive; probe log silent; farmed expired backlog grows;
reconcile never runs.

| Unit | Schedule | Script | Policy |
|------|----------|--------|--------|
| `grok-farm-probe.timer` | ~6h | `scripts/probe_soft.sh` | soft only; **no** `--mark-error` |
| `grok-farm-reconcile.timer` | ~12h | `scripts/reconcile_soft.sh` | report only |
| `grok-farm-mark-expired.timer` | 30m | `scripts/mark_expired_farmed.sh` | **farmed-only** JWT → error |

```bash
systemctl list-timers 'grok-farm-*' --no-pager
sudo systemctl start grok-farm-probe.service
sudo systemctl start grok-farm-reconcile.service
sudo systemctl start grok-farm-mark-expired.service
# enable after deploy:
sudo systemctl enable --now grok-farm-probe.timer
sudo systemctl enable --now grok-farm-reconcile.timer
sudo systemctl enable --now grok-farm-mark-expired.timer
```

Ban risk: probe is soft inventory only (same as R13). Mark-expired does not touch
injected rows and does not revoke gateway connections.
