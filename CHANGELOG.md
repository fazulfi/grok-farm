# Changelog

All notable changes to Grok Farm are documented here.

## [Unreleased]

### Planned
- Re-farm / re-auth automation for `status=error` + `notes=token_expired` (gateway-side revoke optional)
- Optional health-check systemd timer → webhook when `GROK_ALERT_WEBHOOK` set

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
