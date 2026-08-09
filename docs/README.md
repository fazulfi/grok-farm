# Grok Farm — Documentation Index

**Product:** autofarm + auto-inject into 9router. **Not** a session lifecycle manager (no re-auth / keep-alive after handoff). Soft probe/JWT tools = inventory observability only.

| Document | Audience | Purpose |
|----------|----------|---------|
| [ARCHITECTURE.md](./ARCHITECTURE.md) | Engineers | Topology, data planes, non-goals |
| [OPERATIONS.md](./OPERATIONS.md) | Operators | Day-2, health CLI, brutal v5 auto-inject |
| [DEPLOYMENT.md](./DEPLOYMENT.md) | Platform | Fresh install, systemd, 9router helpers |
| [**MIGRATION.md**](./MIGRATION.md) | **DR / on-call** | **VPS death rebuild PATH A–D (full playbook)** |
| [INTEGRATION-9ROUTER.md](./INTEGRATION-9ROUTER.md) | Integration | Inject, proxyPools, reconcile (not session mgmt) |
| [DATA-MODEL.md](./DATA-MODEL.md) | Engineers | `akun.db` pipeline marks, probe meta (not sessions) |
| [RUNBOOK.md](./RUNBOOK.md) | On-call | Incidents R3–R17 (capacity = re-farm, not reauth) |
| [CAPACITY.md](./CAPACITY.md) | Capacity | Concurrent, multi-VPS |
| [SECURITY.md](./SECURITY.md) | Security | Secrets, age, least privilege |

**Repo:** private `fazulfi/grok-farm`  
**Code pin:** `v2.3.2`+ / `main`  
**Live fleet (2026-07-16):** grok2 `VPS_IP1` · grok3 `VPS_IP2` · grok4 `VPS_IP3` · grok5 `VPS_IP4` · grok6 `VPS_IP5` · user `USER` · concurrent **3** · gateway `GW_IP:39999`  
**OTP domains:** `YOURDOMAIN.com` · `YOURDOMAIN.com` · `YOURDOMAIN.com`
### Quick DR

1. Open **[MIGRATION.md](./MIGRATION.md)**  
2. Prefer PATH A (age backup) or PATH B (`ops/export_9r_to_akun.py` on 9router)  
3. Validate with checklist in MIGRATION §8 / DEPLOYMENT §7  
4. Incident shortcut: RUNBOOK **R16**
