# Changelog

All notable changes to Grok Farm are documented here.

## [Unreleased]

### Added

- **Health-check systemd timer** (`systemd/grok-farm-health.{service,timer}` +
  `scripts/health_check.sh`): every 15 min runs inventory-only
  `check_status.py --json` (no `--probe` / `--mark-error`); logs
  `logs/health_check.log`; webhook via `alerts.py` on exit 2 / failure when
  `GROK_ALERT_WEBHOOK` / `GROK_FARM_ALERT_WEBHOOK` set
- `deploy_farm_vps.sh` installs/enables backup + health timers (farmer unit
  refreshed, **not** restarted)
- Docs: OPERATIONS §1/§2.1/§10, DEPLOYMENT §2.5/§6b, SECURITY Alerts, RUNBOOK **R17**

### Changed

- **Docs product-scope reframe:** Grok Farm = **autofarm + auto-inject only**,
  not a session lifecycle manager. README / AGENTS / ARCHITECTURE non-goals /
  DATA-MODEL pipeline marks / OPERATIONS steady state / RUNBOOK R10–R13 /
  SECURITY soft meta / INTEGRATION-9ROUTER re-inject wording / docs index —
  capacity recovery = re-farm + inject; soft probe/JWT = observability noise

### Planned

- Re-farm / re-auth automation for `status=error` + `notes=token_expired` /
  `needs_relogin` (browser path optional) — **out of scope** for farm-only
  roadmap until operator re-enables

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
