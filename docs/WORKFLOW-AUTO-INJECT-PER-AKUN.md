# WORKFLOW AUTO-INJECT PER AKUN — Grok Farm → 9Router (VERIFIED 2026-08-07)

**Status: PRODUCTION** — farm 100 akun berjalan dgn alur ini.
**Kecepatan:** ~84 detik/akun (farm + token + inject + proxy, 1 proses atomic).

---

## Alur (atomic per akun, tanpa proses terpisah)

```
farm_v2.py (V2_AUTO_INJECT=1)
  ├─ 1. Farm akun: signup x.ai (proxy WebShare rotate) → grok.com
  ├─ 2. OAuth device-code DI browser sama → access_token (expires 6 jam)
  ├─ 3. [AUTO-INJECT] POST /api/oauth/grok-cli/exchange {"code":"<JWT>"}
  │     → connection grok-cli dibuat (authType: access_token)
  ├─ 4. [AUTO-INJECT] PUT /api/providers/<id> {"proxyPoolId":"<pool match IP farm>"}
  │     → egress call konsisten (proxy pool SAMA IP dgn farm → anti-plenger)
  └─ 5. v2_sso.txt append {email, access_token, injected, conn_id, pool_id}
```

## Env (di .env VPS)

```bash
V2_AUTO_INJECT=1
V2_R9_BASE=https://router.markettabrak.biz.id   # atau http://127.0.0.1:20228 (gateway host)
V2_R9_TOKEN=<cli-token>   # 16-char sha256(machineId + "9r-cli-auth" + cliSecret)[:16]
```

## Run

```bash
# 1 akun (test)
xvfb-run -a python3 farm_v2.py 1
# → [oauth] TOKEN diperoleh ... [inject] OK -> conn <id> pool <pool-id> [1] OK <email> (84.0s)

# 100 akun (sequential, ~2.3 jam di 2GB RAM VPS)
nohup bash run_farm100.sh > /tmp/run_farm100.out 2>&1 &
```

## Mengapa per-akun (bukan batch)?

| Faktor | Per-akun | Batch |
|---|---|---|
| **Proxy pool** | 1 connection = 1 pool stabil sejak lahir | Rebutan pool di tengah |
| **Token freshness** | Inject <1 menit setelah dapat (6 jam valid) | Akun pertama token tua 1-2 jam |
| **Failure isolation** | 1 gagal = 1 akun, lanjut | 1 error = seluruh queue blok |
| **Usage visibility** | Billing/quota real-time per connection | Tertunda |
| **Arsitektur** | Sesuai brutal_farmer.sh (MID_DRAIN) | Lawan pola |

## Proxy pool match (kunci anti-plenger)

- Proxy farm: `http://user:pass@<IP>:<port>` (WebShare, rotate per akun)
- 9router proxy pools: `Imported <IP>:<port>` (WebShare sama, di-import)
- `inject_to_9router()` cari pool yang IP-nya == IP proxy farm akun itu
- Fallback: pool Imported pertama

## Verify

```bash
# connection count 9router
TOK=$(...); curl -s http://127.0.0.1:20228/api/providers -H "x-9r-cli-token: $TOK" | \
  python3 -c "import sys,json; d=json.load(sys.stdin); print(len([c for c in d['connections'] if c.get('provider')=='grok-cli' and c.get('isActive')]))"
```

---

*Workflow auto-inject per akun — 2026-08-07. Verified: 1 akun test (84s) + farm 100 running.*
