# METODE INJECT — Grok Farm → 9Router (grok-cli, DENGAN PROXY)

**Status: VERIFIED 2026-08-07** — inject akun farm ke 9Router via API langsung, tanpa browser.
**Call model sukses:** `gcli/grok-4.5` → `255 km` + reasoning lengkap (free account!).

---

## 1. Ringkasan

```
farm_v2.py (Playwright, proxy WebShare) → v2_sso.txt {email, access_token}
    ↓
POST /api/oauth/grok-cli/exchange {"code": "<access_token JWT>"}
    → connection grok-cli dibuat (authType: access_token)
    ↓
PUT /api/providers/<id> {"proxyPoolId": "<pool>"}
    → assign proxy pool (WAJIB — egress konsisten, anti-plenger)
    ↓
Call model via 9Router: POST /v1/chat/completions {"model":"gcli/grok-4.5"}
```

## 2. Endpoint (9router-ind bare-metal)

| Endpoint | Method | Body | Hasil |
|---|---|---|---|
| `/api/oauth/grok-cli/exchange` | POST | `{"code": "<JWT access_token>"}` | `{"success":true,"connection":{id,provider:"grok-cli"}}` |
| `/api/providers/<id>` | PUT | `{"proxyPoolId": "<pool-id>"}` | connection + proxyPoolId |
| `/api/proxy-pools` | GET | — | daftar pool (Imported <ip>:<port>) |
| `/api/providers` | GET | — | daftar connection |

Auth: header `x-9r-cli-token: <16-char sha256(machineId + "9r-cli-auth" + cliSecret)[:16]>`
Lokasi: `http://127.0.0.1:20228` (gateway host) atau `https://router.markettabrak.biz.id` (dari VPS).

## 3. KRITIS — inject TANPA browser (bukan device-code!)

**JANGAN** pakai alur device-code (buka verification page, klik Continue/Allow) — RIBET & butuh browser.
**LANGSUNG** exchange access_token yang SUDAH didapat saat farming:

```bash
curl -X POST http://127.0.0.1:20228/api/oauth/grok-cli/exchange \
  -H "x-9r-cli-token: $TOK" -H "Content-Type: application/json" \
  -d '{"code":"<access_token JWT>"}'
# → {"success":true,"connection":{"id":"...","provider":"grok-cli"}}
```

**Syarat:** `code` harus JWT (diawali `eyJ`, berisi `.`). Jika bukan JWT → dianggap OAuth code → butuh exchange device-code (ribet).

## 4. KRITIS — WAJIB assign proxy pool

Connection tanpa proxy → egress dari IP gateway (EU?) → Grok 4.5 kena region block / plenger.

```bash
# 1. dapat pool (proxy WebShare sudah di-import sebagai "Imported <ip>:<port>")
POOL_ID=$(curl -s .../api/proxy-pools | jq -r '.proxyPools[0].id')
# 2. assign ke connection
curl -X PUT http://127.0.0.1:20228/api/providers/$CONN_ID \
  -H "x-9r-cli-token: $TOK" -H "Content-Type: application/json" \
  -d "{\"proxyPoolId\":\"$POOL_ID\"}"
```

Proxy pool = **WebShare yang sama dengan farm** (user `vjeywwqm`) → egress call konsisten non-EU + fingerprint sama → anti-plenger.

## 5. Run (script siap pakai)

```bash
R9_TOKEN=<cli-token> python3 inject_grok_cli_9router.py --input v2_sso.txt
# akun: N | proxy pools: M (WebShare)
# [i] OK <email> -> conn <id> ; proxy -> Imported <ip>:<port>
# DONE: ok=N fail=0
```

Mode: `--pool-mode per-akun` (default, 1 proxy unik/akun) | `rotate`.

## 6. Verifikasi end-to-end

```bash
# call model via 9Router (api key dari apiKeys table, e.g. hiyuki)
curl https://router.markettabrak.biz.id/v1/chat/completions \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"model":"gcli/grok-4.5","messages":[{"role":"user","content":"..."}]}'
# → grok-4.5 reasoning OK (free account, no subscription!)
```

---

*Metode inject v2 — 2026-08-07. Verified: 2 akun inject + proxy, call grok-4.5 OK.*
