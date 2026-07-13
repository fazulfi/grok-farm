# Capacity Planning — Grok Farm

---

## 1. Reference hardware (production CSA)

| Resource | Spec |
|----------|------|
| vCPU | 4 |
| RAM | 8 GB |
| Swap | 8 GB file |
| Disk | 150+ GB SSD |
| Network | 1 Gbps class VPS |

---

## 2. Resource model

### Per concurrent browser (Camoufox headless)

| Resource | Typical | Peak |
|----------|---------|------|
| RSS | 800 MB–1.2 GB | 1.5 GB |
| CPU | 0.5–1.5 cores when active | 2+ short spikes |

### Fixed overhead

| Component | RSS |
|-----------|-----|
| farm.py + playwright driver | ~200–400 MB |
| OS + caches | ~1–2 GB |

---

## 3. Concurrent sizing

| CONCURRENT | Est. RAM browsers | Recommendation |
|------------|-------------------|----------------|
| 1 | ~1 GB | Dev / debug |
| 2 | ~2–2.5 GB | Safe default |
| **3** | ~3–4 GB | **Prod default (with 8G swap)** |
| 4 | ~4–5 GB | Only if fail rate stays low |
| 5+ | ≥5 GB | Need 16 GB RAM VPS |

---

## 4. Throughput estimate

Assumptions: concurrent 3, ~3–5 min per successful account (OTP + Turnstile variance).

| Metric | Estimate |
|--------|----------|
| Accounts / hour | ~30–50 (optimistic) |
| Accounts / day | ~700–1000 (if uninterrupted) |
| Limiting factors | Turnstile, IMAP, proxy quality, xAI rate limits |

Real-world success rate often **60–90%** of attempts; plan capacity on **successful** accounts.

---

## 5. Proxy capacity

| Pool size | Guidance |
|-----------|----------|
| < 10 | High reuse; Turnstile risk |
| 30–70 | Good diversity for concurrent 3 |
| 100+ | Multi-VPS farming |

Rotating gateway reduces static IP count but may share reputation.

---

## 6. Multi-VPS horizontal scale

```
VPS-F1 ──┐
VPS-F2 ──┼──► same domain/IMAP ──► 9router
VPS-F3 ──┘
```

**Bottlenecks:**

- Single Gmail IMAP inbox (mitigate: multi domain first, then multi Gmail via `identities.json`)
- Catch-all provider limits / domain reputation burn
- 9router SQLite write contention (serialize injects or use one injector)

**Pattern:** one inject coordinator OR staggered batch times.

### 6.1 Identity scale path

| Stage | Config | When |
|-------|--------|------|
| 0 | 1 domain + 1 Gmail | Bootstrap |
| 1 | **N domains + 1 Gmail** | Domain burn / diversity (recommended) |
| 2 | N domains + M Gmail pairs | IMAP rate / isolation |
| 3 | Multi-VPS × stage 1–2 | Throughput; inject serialized |

Env for stage 1: `GROK_EMAIL_DOMAINS=a.com,b.com` (same `GROK_IMAP_*`).

---

## 7. Disk growth

| Artifact | Growth |
|----------|--------|
| screenshots | High if verbose failures |
| batch logs | Medium |
| akun.db | Low (KB–MB) |
| tokens in 9router | Low–medium |

Policy: purge screenshots > 7 days; keep batch logs 30 days.

---

## 8. SLOs (suggested)

| SLO | Target |
|-----|--------|
| Farmer uptime | 99% monthly |
| Inject lag | < 1 batch cycle (~15–30 min) |
| Proxy sync success | 99% |
| Grok 4.5 success via gateway | Track in 9router usage |

---

## 9. Alerts (recommended)

- systemd unit inactive > 5 min
- `farmed` backlog > 50 for > 2 hours
- free RAM < 500 MB and swap use > 50%
- proxyPools count = 0
- 9router health fail
