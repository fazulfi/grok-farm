# Security Policy — Grok Farm

---

## 1. Threat model (summary)

| Asset | Risk if leaked |
|-------|----------------|
| Gmail App Password | Inbox takeover / OTP theft |
| xAI account password | Account takeover |
| OAuth access/refresh tokens | API/CLI abuse until expiry/revoke |
| Proxy credentials | Bandwidth theft / IP burn |
| 9router CLI token | Unauthorized provider mutations |

---

## 2. Hard requirements

1. **Never commit** `.env`, `akun.db`, `results/`, `proxies.txt`, `usa_proxies.txt`
2. **Never run Camoufox/farm as root**
3. Prefer **Gmail App Passwords** over primary password
4. SSH keys for farm→gateway: dedicated key, no passphrase in automation or use ssh-agent carefully
5. 9router dashboard not public without auth

---

## 3. Secrets handling

| Secret | Storage |
|--------|---------|
| `.env` | Server only + encrypted backup |
| `akun.db` | `chmod 600`, owner farmer user |
| Proxy lists | 9router DB; farm file is derived cache |
| GitHub | Public/private repo **without** secrets |

### Backup encryption (recommended)

```bash
# age example
age -r age1... -o akun.db.age akun.db
```

---

## 4. Network

- Outbound HTTPS to `accounts.x.ai`, `auth.x.ai`, `api.x.ai`, `grok.com`, IMAP
- Farm→Gateway: SSH only (port custom)
- Gateway HTTP API: prefer loopback + CLI token; do not expose `20128` publicly without reverse proxy auth

---

## 5. Rotation procedures

| Event | Action |
|-------|--------|
| `.env` leaked | Rotate Gmail app password + GROK_PASSWORD; re-farm critical accounts |
| GitHub secret scan alert | Purge history / rotate all credentials |
| Proxy vendor compromise | Wipe proxyPools; re-import |
| Staff offboarding | Revoke SSH keys; rotate tokens |

---

## 6. Logging hygiene

- Do not ship full JWT in external log aggregators
- `farm.log` may contain emails — treat as sensitive
- Screenshots may show PII — restrict access

---

## 7. Compliance note

Automating account creation may violate third-party ToS. Operators are responsible for legal/compliance review in their jurisdiction. This software is provided for infrastructure automation research and authorized environments only.
