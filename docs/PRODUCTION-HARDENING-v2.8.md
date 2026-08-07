# V2.8 — PRODUCTION HARDENING (VISION TURNSTILE + BURN/RETRY)

**Status: PRODUCTION** — farm 100 supervised running (rate ~94%, 2026-08-07).

---

## Masalah yang dipecahkan

| Masalah | Gejala | Fix |
|---|---|---|
| **Turnstile interactive** | Checkbox click doang gagal (proxy di-flag xAI) | **Vision solver**: screenshot → gpt-4o via 9router → koordinat klik → klik puzzle |
| **Satu context semua akun** | Semua akun pakai proxy #1 → xAI flag | **Context PER AKUN** (proxy rotate) |
| **OTP salah match** | Baca OTP email akun LAIN (subject doang) | **Strict To: match** target email |
| **Proxy burn** | IP WebShare kadang-lolos-kadang-gagal | **Burn+retry**: proxy gagal → skip + coba cadangan (3 attempt) |
| **Crash tengah batch** | Farm mati total | **Supervised loop** (auto-restart, resume-safe) |

## Vision Turnstile Solver

```python
# farm_v2.py — _solve_turnstile(page, max_wait=60)
# 1. klik checkbox iframe challenges.cloudflare.com
# 2. kalau token belum muncul → screenshot → call vision model (9router gpt-4o)
# 3. vision return "CHECKBOX" / "NO_CAPTCHA" / koordinat CLICK x%,y%
# 4. klik koordinat → ulangi sampai token OK

# env (di .env VPS)
GROK_CAPTCHA_MODEL=openrouter/openai/gpt-4o
GROK_CAPTCHA_API_KEY=<api-key-9router-hiyuki>
GROK_CAPTCHA_API_URL=https://router.markettabrak.biz.id/v1/chat/completions
```

**Catatan CF 1010:** vision call via urllib → `403 error code: 1010` (Cloudflare block). Fix: pakai **curl + browser UA** (subprocess), bukan urllib.

## Burn + Retry

```python
# main loop farm_v2.py — 3 attempt per akun
while attempts < 3 and not ok:
    ctx = _launch_ctx(p, load_extension=True)   # proxy rotate (skip burned)
    try:
        res = _signup_one(ctx, chrome_v)
    except Exception as e:
        if "Turnstile" in emsg or "givenName" in emsg or "OTP" in emsg:
            BURNED_PROXIES.add(px_now)          # skip proxy ini selanjutnya
        time.sleep(3)
```

## Supervised launcher

```bash
# run_farm100_supervised.sh — auto-restart, resume-safe (hitung dari v2_sso)
nohup bash run_farm100_supervised.sh > /tmp/run_sup.out 2>&1 &
```

## Hasil verifikasi (test 3 akun)

```
[1] OK hd7f8pg9wum2@gogoligo.my.id (84.1s, attempt 1) — pool 90d34908
[2] OK ucxnzjuxoytu@gogoligo.web.id (141.3s, attempt 1) — pool 96ea5eb3
[3] OK pj6qf195edgb@gogoligo.my.id (262.5s, attempt 2) — pool 8af25d30
DONE: ok=3 total=3   ← 100% success, proxy beda per akun
```

Farm 100 supervised: **16/17 OK = 94%** (1 burn → retry).

---

*v2.8 production hardening — 2026-08-07.*
