# Changelog

All notable changes to Grok Farm are documented here.

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
