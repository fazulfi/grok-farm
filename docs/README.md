# Grok Farm — Documentation Index

| Document | Audience | Purpose |
|----------|----------|---------|
| [ARCHITECTURE.md](./ARCHITECTURE.md) | Engineers | Topology, data planes |
| [OPERATIONS.md](./OPERATIONS.md) | Operators | Day-2, health CLI, brutal v5 auto-inject |
| [DEPLOYMENT.md](./DEPLOYMENT.md) | Platform | Fresh install, systemd, 9router helpers |
| [**MIGRATION.md**](./MIGRATION.md) | **DR / on-call** | **VPS death rebuild PATH A–D (full playbook)** |
| [INTEGRATION-9ROUTER.md](./INTEGRATION-9ROUTER.md) | Integration | Inject, proxyPools, reconcile |
| [DATA-MODEL.md](./DATA-MODEL.md) | Engineers | `akun.db`, proxy/domain stats, probe columns |
| [RUNBOOK.md](./RUNBOOK.md) | On-call | Incidents R3–R16 (R16 = VPS rebuild) |
| [CAPACITY.md](./CAPACITY.md) | Capacity | Concurrent, multi-VPS |
| [SECURITY.md](./SECURITY.md) | Security | Secrets, age, least privilege |

**Repo:** private `fazulfi/grok-farm`  
**Code pin:** `v2.2.0`+ / `main`  
**Live farm (update when moved):** `168.144.137.240` · user `magadirxwin` · gateway `49.12.82.34:39999`

### Quick DR

1. Open **[MIGRATION.md](./MIGRATION.md)**  
2. Prefer PATH A (age backup) or PATH B (`ops/export_9r_to_akun.py` on 9router)  
3. Validate with checklist in MIGRATION §8 / DEPLOYMENT §7  
4. Incident shortcut: RUNBOOK **R16**
