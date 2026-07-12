# Grok Farm

**Enterprise pipeline for xAI / Grok CLI account farming, inventory, and 9router injection.**

| | |
|--|--|
| **Status** | Production |
| **Version** | 2.0.0 |
| **Runtime** | Ubuntu VPS · systemd · Camoufox · SQLite · optional 9router |

## What it does

1. **Farms** Grok / xAI accounts (email OTP → profile → Turnstile → OAuth PKCE tokens)
2. **Stores** inventory in SQLite (`akun.db`) with lifecycle states
3. **Syncs** residential proxies from **9router proxyPools** (source of truth)
4. **Injects** tokens into 9router as **Grok CLI (Grok Build)** + **xAI** connections with per-account random proxy
5. **Runs unlimited** under systemd (`Restart=always`)

```
Catch-all domain → Gmail IMAP → farm.py (Camoufox + proxies)
        → batch files → akun.db → workflow.py → 9router (grok-cli / xai)
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
