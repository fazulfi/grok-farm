# Grok Farm

**Enterprise autofarm + auto-inject pipeline for xAI / Grok CLI OAuth credentials into 9router.**

| | |
|--|--|
| **Status** | Production |
| **Version** | 2.3.2 |
| **Runtime** | Ubuntu VPS · systemd · Camoufox · SQLite · optional 9router |
| **Product scope** | **Farm → inject only** — not a session lifecycle manager |

## What it does

1. **Farms** Grok / xAI accounts (email OTP → profile → Turnstile → OAuth PKCE tokens)
2. **Auto BSF-gate** — tiap akun di-scan JWT (`bfs` / `bot_flag_source`) saat save; akun flag (plenger) dibuang ke `results/plenger.txt`, tidak masuk `accounts.txt`. Hasil sukses otomatis BSF-1/BSF-2 = 0.
3. **Records** pipeline state in SQLite (`akun.db`: `farmed` → `injected` / `error`) — inject queue marks, not session management
4. **Syncs** residential proxies from **9router proxyPools** (source of truth)
5. **Injects immediately** into 9router as **Grok CLI (Grok Build)** + **xAI** (on-path; steady state `farmed` ≈ 0)
6. **Runs unlimited** under systemd (`Restart=always`)

**Out of scope:** browser re-auth, refresh-token session recovery, keeping injected tokens “alive” after handoff. After inject, token alive/dead is a **9router / consumer** concern. Soft probe/JWT meta on the farm is optional inventory noise only.

```
Catch-all domain → Gmail IMAP → farm.py (Camoufox + proxies)
        → batch files → akun.db → workflow.py → 9router (grok-cli / xai)
        (farmed backlog drains in the same loop — no session lifecycle)
```


## Quick start

```bash
git clone https://github.com/fazulfi/grok-farm.git
cd grok-farm
chmod +x install.sh run.sh brutal_farmer.sh scripts/*.sh
./install.sh
cp .env.example .env && nano .env
./run.sh
# production:
sudo cp systemd/grok-farmer.service /etc/systemd/system/
# adjust User= / paths
sudo systemctl enable --now grok-farmer
```

**Non-root user required** (Camoufox breaks under root).

## Documentation

| Doc | Description |
|-----|-------------|
| [AGENTS.md](./AGENTS.md) | Agent operating contract (workflow, safety, DoD) |
| [docs/ARCHITECTURE.md](./docs/ARCHITECTURE.md) | System design |
| [docs/OPERATIONS.md](./docs/OPERATIONS.md) | Day-2 ops |
| [docs/DEPLOYMENT.md](./docs/DEPLOYMENT.md) | Fresh install |
| [docs/MIGRATION.md](./docs/MIGRATION.md) | VPS rebuild / DR |
| [docs/INTEGRATION-9ROUTER.md](./docs/INTEGRATION-9ROUTER.md) | Inject contract |
| [docs/DATA-MODEL.md](./docs/DATA-MODEL.md) | Schemas |
| [docs/RUNBOOK.md](./docs/RUNBOOK.md) | Incidents |
| [docs/CAPACITY.md](./docs/CAPACITY.md) | Sizing |
| [docs/SECURITY.md](./docs/SECURITY.md) | Secrets |
| [CHANGELOG.md](./CHANGELOG.md) | History |

## Layout

```
farm.py, brutal_farmer.sh, workflow.py, sync_proxies_from_9r.py, import_db.py
systemd/   ops/   scripts/   docs/
```

## Migrate dead VPS

```bash
./scripts/deploy_farm_vps.sh user@NEW_VPS
./scripts/restore_farm.sh user@NEW_VPS ./backups/grok-farm-backup-XXXX.tgz
ssh user@NEW_VPS 'cd ~/grok-farm && python3 workflow.py && sudo systemctl enable --now grok-farmer'
```

## Security

Never commit `.env`, `akun.db`, `results/`, or proxy credential files. See [docs/SECURITY.md](./docs/SECURITY.md).

## License

MIT — see [LICENSE](./LICENSE).
