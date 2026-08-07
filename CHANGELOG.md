# Changelog

All notable changes to Grok Farm are documented here.

## [v2.8.1] — 2026-08-07

### Changed
- **Vision Turnstile model → `cx/gpt-5.6-luna`** (wajib — user spec). Sebelumnya `openrouter/openai/gpt-4o`.
  - Default di `farm_v2.py` + `.env` (GROK_CAPTCHA_MODEL).
  - Verified: `cx/gpt-5.6-luna` vision call OK via 9router (baca screenshot Turnstile).
- Farm 100 supervised restart dgn model baru (resume dari 20 akun).

## [v2.8.0-production] — 2026-08-07

### Added — PRODUCTION HARDENING
- **Vision Turnstile solver** (`_solve_turnstile`): checkbox click → screenshot → gpt-4o via 9router → koordinat klik → solve interactive puzzle. (Checkbox doang gagal di proxy di-flag.)
- **Context PER AKUN** (proxy rotate benar): satu context utk semua akun = semua pakai proxy #1 = xAI flag.
- **OTP strict match**: `To:` persis target (bukan match subject doang — bug baca OTP email akun lain).
- **Burn + retry**: proxy gagal (Turnstile/givenName/OTP) → skip (BURNED_PROXIES) + retry 3 attempt.
- **Supervised launcher**: `run_farm100_supervised.sh` — auto-restart on crash, resume-safe.
- `docs/PRODUCTION-HARDENING-v2.8.md` — dokumentasi lengkap.
- Vision call via **curl + browser UA** (urllib kena CF 1010).

### Verified
- Test 3 akun: **3/3 OK** (100%), proxy beda per akun, inject+pool OK.
- Farm 100 supervised: **16/17 OK = 94%** (1 burn → retry sukses).

## [v2.7.0-auto-inject] — 2026-08-07

### Added
- **`docs/WORKFLOW-AUTO-INJECT-PER-AKUN.md`** — workflow inject per akun (atomic, VERIFIED):
  - Farm → OAuth token (di browser sama) → inject 9router (exchange JWT) → assign proxy pool match IP farm.
  - 1 proses, ~84 detik/akun. Keputusan: **per-akun > batch** (proxy stabil, token fresh, failure isolated).
- **`farm_v2.py`** — hook `V2_AUTO_INJECT` + `inject_to_9router()` (exchange + proxy pool matching):
  - `V2_AUTO_INJECT=1`, `V2_R9_BASE`, `V2_R9_TOKEN` di .env.
  - Log: `[oauth] TOKEN diperoleh ... [inject] OK -> conn <id> pool <pool-id>`.
- `run_farm100.sh` — launch farm 100 akun (sequential, log /tmp/farm100.log).

### Verified
- 1 akun test: `u3un21sf6yfe@gogoligo.web.id` → token ✓ → injected ✓ → conn `f0dad024-...` pool `90d34908-...` (84.0s).
- Farm 100 akun running (2026-08-07 12:52 UTC).

## [v2.6.0-inject] — 2026-08-07

### Added
- **`docs/METHOD-INJECT-2026-08-07.md`** — metode inject akun farm ke 9Router (grok-cli)
  **tanpa browser, DENGAN proxy** — VERIFIED:
  - `POST /api/oauth/grok-cli/exchange {"code":"<JWT>"}` → connection dibuat langsung (authType access_token).
  - `PUT /api/providers/<id> {"proxyPoolId":...}` → assign proxy pool WAJIB (anti-plenger, egress non-EU).
  - Call `gcli/grok-4.5` via 9Router → **reasoning sukses (255 km)** — akun FREE, tanpa subscription.
- **`inject_grok_cli_9router.py`** v2 — rewrite bersih: direct-JWT exchange + proxy pool assignment
  otomatis (per-akun / rotate). Input v2_sso.txt (JSONL) atau accounts.txt.
- Proxy pools WebShare (`Imported <ip>:<port>`) sudah di-import di 9router (191 pool).

### Verified
- `R9_TOKEN=... python3 inject_grok_cli_9router.py --input v2_sso.txt`
  → `[1] OK af71o796cxxk@gogoligo.biz.id -> conn ... ; proxy -> Imported 46.202.227.181:8188 ; DONE: ok=1 fail=0`
- 3 connection grok-cli active (1 manual + 2 inject), call grok-4.5 OK.

## [v2.5.0-verified] — 2026-08-07

### Added
- **`docs/METHOD-2026-08-07.md`** — metode resmi yang **TERBUKTI** (production-tested 1 akun
  sukses end-to-end: `e0sk2n2atc7u@gogoligo.my.id` → grok.com, 71.9s). Dokumentasi lengkap:
  - Playwright `channel='chrome'` + headed/xvfb + WebShare proxy **split-creds** (bukan URL-embedded — ERR_INVALID_AUTH_CREDENTIALS).
  - CF Email Routing catch-all → Gmail (domain gogoligo.*, 1 Gmail shared).
  - **OTP IMAP via proxy CONNECT tunnel** (idcloudhost SG blokir 993 langsung).
  - **OTP input keyboard typing** (6 kotak React, `.fill()` gagal).
  - **Turnstile solve via iframe `challenges.cloudflare.com`** (checkbox cookie banner ≠ Turnstile).
  - `since_ts` filter OTP fresh (hindari re-used/invalid).
- `farm_v2.py` v2.5: semua fix di atas (proxy split-creds, IMAP via CONNECT, keyboard OTP, turnstile iframe).

### Verified
- `python3 farm_v2.py 1` → `[1] OK e0sk2n2atc7u@gogoligo.my.id (71.9s)`, URL grok.com, 28 SSO cookies.

## [v2.4.0-audit] — 2026-08-07

### Added
- **Audit metode vs realita xAI 2026-08-07** (`docs/AUDIT-2026-08-07.md`): probe live
  (device_authorization_endpoint aktif, vector-code returns 200 w/ user_code, expires_in 1800,
  token poll → `authorization_pending`), scope OIDC resmi, dan kesimpulan **free-tier
  nilainya turun** (grok-4.5/grok-build digerbang di SuperGrok / X Premium+; issue #26847).
- **Prototipe metode baru `farm_v2.py`** (modul paralel — TIDAK menyentuh farm.py/akun.db):
  - Engine: Playwright `channel='chrome'` (ganti Camoufox click-only).
  - Email: MAILLDEZ-compatible temp-mail API (`V2_MAILLDEZ_URL`/`V2_MAILLDEZ_DOMAINS`) — bukan IMAP.
  - Token: **OAuth device-code resmi** `auth.x.ai/oauth2/device/code` → `xai_poll_token` (headless, tanpa callback server).
  - 9Router: `--router` pakai SSO cookies + `/api/oauth/grok-cli/*` device-code consent automation.
  - `--dry-run`: validasi flow + reach device-code live tanpa bikin akun (DIVERIFIED OK).
- `turnstilePatch/` (manifest.json + script.js) di-copy ke repo — **fallback eksperimen saja**.
- `setup_v2.sh` — one-shot prep (Chrome + deps + verifikasi) untuk VPS farm baru.
- `.env.v2.example` — blok config `V2_*` (MAILLDEZ, password, 9Router, toggle patch).
- `docs/RESEARCH-2026-08-07-turnstilepatch.md` — verifikasi independen metode turnstile.

### Changed
- **KOREKSI KRITIS:** turnstilePatch **bukan lagi solusi** — bug Chromium #40280325
  (dasar teknik ini) **di-fix Google Sept 2025** (CL 6917162, rilis ~Chrome 131/132).
  Di Chrome modern patch malah menimpa nilai screenX/Y yang benar (anomali JS).
  Yang menentukan lolos Turnstile 2026: `channel='chrome'` + headed/xvfb + residential IP.
  `farm_v2.py` kini `V2_TURNSTILE_PATCH=0` (default off); opsi driver lanjutan: nodriver/patchright.
- **INTEL FARMER 2026-08-07 — token health (`bfs`/`bot_flag_source`):** akun yang ke-flag
  punya `"bfs":1` (atau `"bot_flag_source":1`) di payload JWT → thinking TIDAK jalan
  (plenger). Akun sehat: scope `... conversations:read conversations:write` + referrer
  (`grok-build`/`cli-proxy-api`). Ditambahkan:
  - **`scan_tokens.py`** — decode JWT, deteksi flag, laporan distribusi scope/referrer.
    Exit 2 kalau ada flag (buang); saran ganti domain kalau semua ke-flag.
  - `farm_v2.py`: `V2_OAUTH_SCOPE` default + `conversations:*`, `V2_OAUTH_REFERRER=grok-build`,
    `V2_CLIENT_VERSION` header — samakan dengan grok-build latest.
- Doc sync: audit + prototype dicatat; production `farm.py` TIDAK diubah (Opsi A — modul paralel).

## [Unreleased]


### Changed

- **Fleet rebuild (2026-07-16):** prior DO trial hosts dead → new 5×8G SGP1
  fleet: `grok2` `157.245.55.62`, `grok3` `168.144.36.46`, `grok4`
  `104.248.157.41`, `grok5` `168.144.37.202`, `grok6` `167.71.208.99`
  (all concurrent **3**; mid-drain only grok4; digest leader grok3). OTP
  domains (3): `markettabrak.biz.id` + `markettabrak.my.id` +
  `markettabrak.site` (Pattern B multi-IMAP). Dead hosts/domains purged from
  inventory docs (AGENTS/CAPACITY/OPERATIONS/DEPLOYMENT/MIGRATION).

### Added

- **Proxy re-enable CLI:** `reenable_proxy.py` (`--list` / `--match` / `--all` /
  `--dry-run`) + `db_schema.list_soft_skipped_proxies` / `reenable_proxy` /
  `reenable_proxies`. Local `proxy_stats` only — **never** gateway DELETE.
  Fail path auto-sets `disabled=1` when `consecutive_fails >= thr`. RUNBOOK **R18**.
- **Sticky fleet digest Telegram card:** leader uses `editMessageText` via
  `alerts.send_or_edit_sticky` so daily fleet digest **updates one message**
  instead of spamming. State: `~/.config/grok-farm/telegram_sticky_fleet_digest.json`
  (chmod 600). `GROK_FLEET_DIGEST_STICKY=0` forces new message. RUNBOOK **R20**.
- **Single ops dashboard (Telegram):** one fleet sticky card updated **per
  farm/inject batch** via `daily_digest.publish_ops_event` + S3-shared
  `message_id` (`farm-vps/fleet-digest/sticky_fleet_digest.json`). Default
  `GROK_OPS_DASHBOARD=1` silences per-batch spam (`GROK_BATCH_ALERTS=0`).
  Card is a **true global fleet dashboard** (KPI / LIVE / HOSTS / PROXY) — not
  a log dump and **not per-VPS cards**. Soft inventory collapsed. All Telegram
  alerts (health hard included) call `publish_ops_event` → aggregate S3
  snapshots → edit the **same** sticky mid (1 message only; delete orphan on
  recreate). Title: `Grok Farm · Fleet dashboard · YYYY-MM-DD`. RUNBOOK **R20**.

### Planned

- Re-farm / re-auth automation for `status=error` + `notes=token_expired` /
  `needs_relogin` (browser path optional) — **out of scope** for farm-only
  roadmap until operator re-enables
- Gateway proxyPools auto-DELETE / `isActive=0` write-back — **manual/CLI only**
  (never product auto-evict)

## [2.3.2] — 2026-07-14

### Added

- **Daily digest + proxy dashboard (Telegram):** `daily_digest.py` +
  `scripts/daily_digest.sh` + `systemd/grok-farm-digest.{service,timer}`
  (~01:00 UTC + ≤5m random). Health inventory + **proxy dashboard**
  (soft-skip/disabled, fail taxonomy, top/worst redacted, domain_stats).
  Deploy installs digest timer. Zero ban risk (inventory only).
- **Fleet-wide digest (1 Telegram/day):** when `~/.config/grok-farm/backup.env`
  is present, each host uploads a redacted snapshot to
  `s3://…/farm-vps/fleet-digest/YYYY-MM-DD/<host>.json`; **leader** waits for
  peers (`GROK_FLEET_DIGEST_WAIT_SEC`, default 900) and posts **one** fleet
  card. Followers do not send Telegram. Flags: `--local` (per-host card),
  `--fleet`, `--no-wait`. Env: `GROK_FLEET_DIGEST*`, `GROK_HOST_TAG`
  (see `.env.example`). RUNBOOK **R20** updated.

### Changed

- Digest timer `RandomizedDelaySec` **20m → 5m** so fleet followers land inside
  leader wait window.

## [2.3.1] — 2026-07-14

### Added

- **Multi-VPS farmer fleet docs:** live inventory for DO hosts
  grok4/grok3/grok5/grok6 in `AGENTS.md` §3/§9, `docs/CAPACITY.md` §6.2,
  `docs/OPERATIONS.md` §1 — 8G hosts concurrent **3** (mid-drain on grok4 only);
  **grok6** 4G light concurrent **1**; key-only SSH; off-box farmer key backup
  for trial VPS
- **Age/S3 backup on all DO farmers:** per-host
  `~/.config/grok-farm/backup.env` + `age.pubkey`; S3 prefix
  `farm-vps/grok{N}`; hourly timer verified Upload OK
- **Per-batch farm + inject Telegram alerts:** `farm.py` end-of-batch and
  `workflow.py` inject include full account email lists (ok/fail + reason /
  fail_class), host, batch; `skip_debounce=True`; never JWT/password; HUD
  remains primary UI; `alerts.send_alert(..., skip_debounce=)` +
  `format_email_list()`
- **Telegram rich HTML cards:** `alerts.py` uses `parse_mode=HTML` with
  emoji level header, bold title, key=value chips, `<code>` emails; plain
  fallback if entities rejected; Discord keeps markdown; auto-load `.env`
- **Telegram message effects + denser cards:** private-chat
  `message_effect_id` by level using **free** effects only (🎉 party success /
  👍 thumbs info / 🔥 fire warn+crit+error — Premium-only IDs rejected);
  HTML `<blockquote>` meta strip, section emoji banners, unicode status bar;
  farm/inject ok level=`success`; `GROK_TELEGRAM_EFFECTS=0` to disable;
  effect stripped automatically if API rejects

### Fixed

- **S3 backup double host prefix:** when `S3_PREFIX=farm-vps/grokN`,
  `s3_backup.sh` no longer appends `/hostname` again (was
  `farm-vps/grokN/grokN/...`). Host root is now de-duplicated; retention uses
  the same root via `S3_HOST_ROOT`. Optional `S3_HOST_KEY` if hostname ≠ tag.

### Changed

- **8G fleet concurrent 3:** grok3/grok4/grok5 `GROK_CONCURRENT=3` (prod default
  with 8G swap); grok6 stays concurrent **1** (4G light)
- **CSA retired** (`168.144.137.240`): production fleet is DO grok3–grok6 only;
  docs host maps updated (AGENTS / CAPACITY / OPERATIONS)

## [2.3.0] — 2026-07-14

### Added

- **Health-check systemd timer** (`systemd/grok-farm-health.{service,timer}` +
  `scripts/health_check.sh`): every 15 min runs inventory-only
  `check_status.py --json` (no `--probe` / `--mark-error`); logs
  `logs/health_check.log`
- **Hard/soft health split:** `check_status` exit **0** healthy, **2** soft-only
  (`needs_relogin`, `many_expired_tokens`, `proxy_pool_low`, …), **3** hard
  (`farmer_not_active`, `proxy_file_empty`, `disk_high`, backlog, backup, …).
  Alerts fire on **hard (3)** / unexpected failure only; soft is log-only
- **Alert debounce** (`GROK_ALERT_DEBOUNCE_MIN`, default 60 min) via
  `logs/alert_debounce/` fingerprint files
- **Telegram alerts:** `GROK_TELEGRAM_BOT_TOKEN` + `GROK_TELEGRAM_CHAT_ID`
  (Bot API `sendMessage`); Discord webhook still supported; never log token
- **Proxy soft-evict (fail-open):** `proxy_stats.consecutive_fails` + `disabled` +
  `last_fail_reason`; `proxies_to_skip()`; score-weighted pick on **inject**
  (`workflow.pick_proxy`) **and farm** (`farm.next_proxy`); fail taxonomy
  (`classify_proxy_fail`); never auto-DELETE gateway `proxyPools`
- **Sync soft-filter:** gateway `ops/list_proxies.py` skips inactive / bad
  `testStatus` when present; fail-open if filter empties; `sync_proxies_from_9r`
  ignores `COUNT=` / `#` lines
- **Empty local proxy file:** `brutal_farmer.sh` skips farm round (inject still
  fail-closed on empty proxyPools)
- **Soft timers:** `grok-farm-probe` (6h, no `--mark-error`),
  `grok-farm-reconcile` (12h, report only), `grok-farm-mark-expired` (30m,
  farmed-only)
- `deploy_farm_vps.sh` installs/enables backup + health + probe + reconcile +
  mark-expired timers (farmer **not** restarted)

### Changed

- **Docs product-scope reframe:** Grok Farm = **autofarm + auto-inject only**,
  not a session lifecycle manager. README / AGENTS / ARCHITECTURE non-goals /
  DATA-MODEL pipeline marks / OPERATIONS steady state / RUNBOOK R10–R13 /
  SECURITY soft meta / INTEGRATION-9ROUTER re-inject wording / docs index —
  capacity recovery = re-farm + inject; soft probe/JWT = observability noise
- Health unit `SuccessExitStatus=2 3` so soft/hard inventory outcomes do not
  mark the oneshot unit failed
- Farm path writes `proxy_stats` (best-effort) so soft-skip learns from farm
  failures, not inject-only

## [2.2.2] — 2026-07-13

### Added

- **Large soft probe batch** (`probe_accounts` / `probe_tokens.py`): unprobed-first
  resume order, `--delay` / `GROK_PROBE_DELAY`, progress every N, periodic commit
  (`GROK_PROBE_COMMIT_EVERY`), `--quiet` / `--id-order`
- **`check_status` soft_policy dashboard**: JWT offline % + live probe coverage %,
  needs_relogin %, alive % of probed (meta only; no status flip)
- `.env.example`: `GROK_PROBE_DELAY`, `GROK_PROBE_PROGRESS_EVERY`, `GROK_PROBE_COMMIT_EVERY`

### Changed

- Operator P0 health commands (soft inventory; no default `--mark-error`):

  ```bash
  python3 probe_tokens.py --limit 200
  python3 probe_tokens.py --limit 200 --json
  python3 check_status.py
  python3 reconcile_9router.py
  python3 reconcile_9router.py --json
  ```

- RUNBOOK **R13** / OPERATIONS: large soft probe + soft_policy dashboard notes

### Security

- Soft probe still never flips `injected`→`error` unless explicit `--mark-error`
- Progress/logs never print JWT or Bearer tokens

## [2.2.1] — 2026-07-13

### Added
- **DR playbook rewrite** (`docs/MIGRATION.md`): PATH A age restore, PATH B rebuild `akun.db` from 9router, PATH C warm cutover, PATH D cold; live host map; validation gate; operator go-bag
- `ops/export_9r_to_akun.py` — export active `grok-cli` connections → farm `akun.db` schema (run on gateway; no token print)
- RUNBOOK **R16** farm VPS dead / rebuild pointer
- `docs/DEPLOYMENT.md` production parity (least-privilege sudoers, quoted secrets, log ownership, backup units)
- **Local age backup** (`scripts/local_age_backup.sh` + `systemd/grok-farm-backup.{service,timer}`): hourly encrypt to `~/grok-farm/backups/*.tgz.age` when S3 `backup.env` absent; writes `logs/s3_backup.log` for health CLI; auto-delegates to `s3_backup.sh` if keys present
- Timer path for **offsite S3 age autobackup**: when `~/.config/grok-farm/backup.env` present, hourly unit runs `local_age_backup.sh` → `s3_backup.sh` → dated + `latest.tgz.age` + `LATEST.txt` under `s3://grok-farm/farm-vps/...` (retention via boto3)
- `s3_backup.sh` prefers farm `.venv` python (boto3) and packs probe/adaptive/reconcile modules into archive
- `workflow.py` accepts `GROK_9R_KEY` / `GROK_9R_PORT` aliases (live .env) in addition to `GROK_9R_SSH_KEY` / `GROK_9R_SSH_PORT`
- `brutal_farmer.sh` **v5**: source `.env` + `.venv` python; **auto import+workflow** after each batch with writable log fallback; optional **mid-batch drain** (`GROK_MID_DRAIN_INTERVAL`, default 120s) so 9router gets accounts before batch ends; surface SUMMARY into `farm_brutal.log`
- AGENTS live host map updated for CSA `168.144.137.240` + DR pointer R16

### Fixed
- **Auto inject broken on new VPS**: `workflow.log` / `farm_brutal.log` owned by **root** → `Permission denied` on `>> workflow.log` so post-batch import/inject never ran (farmer user). Chown logs to farmer; brutal falls back to `workflow_user.log` if unwritable.

### Security
- Offsite backup path remains encrypt-before-upload (`BACKUP_ENCRYPT=age`); `backup.env` stays VPS-only (never git)
- DR export helper never prints tokens (counts only)

## [2.2.0] — 2026-07-13

### Added
- **Live JWT probe** (`token_util.probe_access_token` / `probe_accounts`, CLI `probe_tokens.py`): `GET https://api.x.ai/v1/models` with Bearer access token; soft meta only by default
- Account columns: `last_probe_at`, `last_probe_status`, `last_probe_http`, `needs_relogin` (via `ensure_probe_columns` in `db_schema.migrate`)
- Probe statuses: `alive` | `needs_relogin` | `rate_limited` | `spend_limited` | `network_error` | `invalid` | `missing` | `jwt_expired`
- `check_status.py --probe` / `--probe-limit N`: inventory probe + soft `needs_relogin` issue; print probe breakdown
- **9router reconcile**: `ops/list_grok_connections.py` (gateway JSONL, no tokens) + farm `reconcile_9router.py` (SSH diff buckets)
- **Adaptive concurrent** (`adaptive_concurrent.py`): scores `domain_stats` + `proxy_stats` → concurrent in \[MIN, MAX\]; `brutal_farmer.sh` reads `--print` each batch
- RUNBOOK **R13** probe, **R14** reconcile, **R15** adaptive concurrent
- INTEGRATION-9ROUTER §8 inventory reconcile
- `.env.example`: `GROK_ADAPTIVE_CONCURRENT`, `GROK_CONCURRENT_MIN/MAX`, `GROK_PROBE_*`

### Changed
- Soft inventory policy extended: live probe never flips `injected`→`error` unless explicit `--mark-error`
- Offline JWT `expired` still skippable for live HTTP (`GROK_PROBE_SKIP_EXPIRED=1` default)
- Health CLI exit 2 includes soft `needs_relogin` when any account flagged

### Security
- Probe/reconcile never log full JWT or gateway tokens; list helper prints `has_token` only
- Reconcile default is dry report; `--write-notes` soft; `--mark-error` farmed-only

## [2.1.2] — 2026-07-13

### Added
- **S3 retention via boto3** (`s3_upload.py --retention`): no `aws` CLI dependency; keeps `latest.*` + `LATEST.txt`; deletes dated `*.tgz.age` / leftover plaintext older than `RETENTION_DAYS`
- RUNBOOK **R11** multi-Gmail Pattern B (Cloudflare destination + `identities.json`); **R12** retention + Windows age CLI
- `identities.example.json` Pattern B comments; backup packs `identities.json` into encrypted archive when present
- `.env.example` documents `RETENTION_DAYS=14`

### Changed
- `s3_backup.sh` calls boto3 retention after upload (replaces fragile aws-cli subprocess)
- Expired injected JWT policy docs: soft meta default is **recommended enterprise** (preserve gateway IDs)
- SECURITY: Windows age install paths; multi-Gmail secret hygiene; alerts leave webhook unset if unused
- OPERATIONS: multi-Gmail Pattern B ops + S3 retention workflow

### Security
- Multi-Gmail App Passwords stay VPS-only (`identities.json` mode 600); never commit real credentials
- Encrypted S3 backups include `identities.json` when present (age-encrypted archive)

## [2.1.1] — 2026-07-13

### Added
- **Realistic email local-parts** (`name_gen.py`): human-style `first.last` / `j.smith92` instead of crypto hash
- `GROK_EMAIL_LOCAL_STYLE=realistic|crypto|parser` (default `realistic`)
- Optional [parser.name](https://parser.name) API (`GROK_PARSER_NAME_API_KEY`) with offline fallback (free tier ~100/day) — optional spice only; not required for farming
- Offline name corpora under `data/`: Indonesian (`names_id.txt` from [maulvi gist](https://gist.github.com/maulvi/e443e22b82a1dc24e14344b47f0a80ea)), EN first/last, intl; `GROK_NAME_REGION=mixed|id|en|intl`
- Expanded corpora `data/names_extra_first.txt` / `names_extra_last.txt` (~20k first / ~40k last); optional `GROK_NAME_FIRST_FILES` / `GROK_NAME_LAST_FILES`
- **Expired JWT policy**: `token_util.mark_expired_accounts`, CLI `mark_expired_tokens.py` (`--dry-run`, `--include-injected`, `--json`)
- `check_status.py --mark-expired` / `--mark-expired-injected` health hooks
- SECURITY: operator laptop vault path for `age.identity` (Windows ACL note; no USB required)
- RUNBOOK **R10** — expired JWT inventory cleanup

### Changed
- `import_db.py` / `workflow.import_batches` **fail-closed**: dead/invalid JWT insert as `status=error` (`notes=token_expired|bad_token`), not `farmed`
- Default farm local-part style is realistic offline (no external API dependency)

### Security
- age private identity second copy expected on operator laptop (`~/.config/grok-farm/age.identity`, mode 600 / Windows ACL restricted)
- Expired tokens no longer re-queue forever through inject; historical injected JWT expiry remains soft health noise unless `--include-injected`

## [2.1.0] — 2026-07-13

### Added
- `AGENTS.md` agent operating contract: authority order, live-safe VPS ops, security S1–S10, hardening backlog, docs-sync matrix
- Super-autopilot workflow mandates (blocking): **unlimited sub-agents**, **unlimited todos**, **wajib spek enterprise** (§6.1–§6.3)
- Enterprise hardening modules: `log_redact.py`, `token_util.py`, `alerts.py`, `db_schema.py`
- `proxy_stats` table + inject success/fail scoring
- Account columns `token_exp`, `token_health` (JWT exp classification)
- Expanded `check_status.py` health CLI (farmer, disk, proxies, backup age, tokens, proxy scores, domain pool, `--json`)
- Optional `GROK_ALERT_WEBHOOK` / `GROK_FARM_ALERT_WEBHOOK` Discord-style alerts
- S3 backup encryption with `age` (`scripts/s3_backup.sh`, `scripts/s3_upload.py`); restore decrypt path
- Optional age encryption for `scripts/backup_farm.sh` / `scripts/restore_farm.sh`
- **Multi-domain / multi-IMAP identity plane** (`email_identity.py`): `GROK_EMAIL_DOMAINS`, domain strategy, optional `identities.json`
- `identities.example.json` template; health CLI domain pool + inventory counts
- Docs workflow: multi-domain enable, verify, multi-Gmail Pattern B (OPERATIONS/ARCHITECTURE/CAPACITY/RUNBOOK)
- **`domain_stats` table** + OTP/farm success-fail scoring; auto-skip dead catch-all domains (`GROK_DOMAIN_MAX_CONSECUTIVE_FAILS`, default 3)
- Health CLI `domain_stats:` section; RUNBOOK **R3c** (catch-all OTP probe) + **R3d** re-enable procedure
- age identity vault guidance without USB (password manager / second host / encrypted local copy)

### Changed
- Definition of Done / anti-patterns require enterprise-spec compliance and forbid artificial agent/todo caps
- `import_db.py` / `workflow.py` use shared migrate, redaction, token health skip for expired tokens
- `farm.py` email generation + IMAP OTP use identity pool (per-domain IMAP)
- Docs: SECURITY, DATA-MODEL, OPERATIONS, `.env.example` for hardening + multi-domain surface
- `workflow.py`: commit inject marks **before** proxy_stats updates (prevents gateway/DB desync on scoring errors)
- `db_schema.record_proxy_result`: accept `sqlite3.Row` **or** plain tuple
- `check_status.py`: hard issue only for `farmed_expired_tokens`; historical injected JWT age → soft `many_expired_tokens`
- `email_identity.pick_domain` **round_robin**: strict A→B→A→B among healthy candidates (thread-safe); default multi-domain strategy recommendation = `round_robin`
- `workflow.py`: single DB connection + Row factory for farmed scan; mark `bad_token`/`token_expired` as `error` without reopen thrash; inject UPDATE only `status='farmed'`; fail if remote inject exits non-zero with empty stdout
- Offsite backup default: encrypted `.tgz.age` only (plaintext historical S3 objects cleaned on release)

### Security
- Offsite backups encrypt-before-upload (default `BACKUP_ENCRYPT=age`)
- `akun.db` hardened to mode 600 on import/migrate
- Log/webhook redaction for JWT and proxy credentials
- Document least-privilege sudo (revoke `NOPASSWD:ALL` for farmer user)
- age private key must not live only on the farm VPS; operator vault required for restore

## [2.0.0] — 2026-07-12

### Added
- Unlimited systemd farmer loop (`brutal_farmer.sh` + `grok-farmer.service`)
- SQLite inventory `akun.db` with `farmed` / `injected` states
- Unified workflow: import → inject to 9router (`workflow.py`)
- Proxy source-of-truth from 9router `proxyPools` (`sync_proxies_from_9r.py`, `ops/list_proxies.py`)
- Bulk Grok CLI inject (`ops/grok_cli_bulk_inject.py`) — panel **Grok CLI (Grok Build)**
- Dual inject: `grok-cli` + `xai` provider connections
- Enterprise docs: architecture, ops, runbook, capacity, security, deployment, migration
- Deploy/backup/restore helper scripts under `scripts/`

### Changed
- Production default concurrent browsers: 3 (with 8G swap guidance)
- Headless mode for VPS; non-EU proxies required for Grok 4.5

### Security
- `.gitignore` hardened against secrets and runtime DBs
- Documented non-root Camoufox requirement

## [1.0.0] — prior

### Added
- Standalone `farm.py` CLI farmer (OTP, Turnstile, OAuth PKCE)
- Batch result folders, HUD UI, proxy file support
- `install.sh` / `run.sh`
