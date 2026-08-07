# Verifikasi Independen — turnstilePatch di 2026 (accounts.x.ai)

**Status:** RISET SELESAI — temuan teknis diverifikasi terhadap sumber primer (Chromium issue tracker, gerrit, GitHub, webscraper.io).
**Tanggal riset:** 2026-08-07
**Fokus:** Apakah turnstilePatch (MV3 extension patch `MouseEvent.prototype.screenX/screenY`) masih jadi metode terbaik untuk melewati Cloudflare Turnstile di signup `accounts.x.ai` tahun 2026, tanpa paid captcha service.

---

## 1. KESIMPULAN (TL;DR)

**turnstilePatch BUKAN lagi metode yang andal/terbukti di 2026 — dan malah berisiko jadi tanda keanehan.**

Akar teknisnya (Chromium CDP bug) **sudah DIPATCH oleh Google pada Sept 2025**, sehingga premis utama extension ini sudah mati di semua Chrome modern. Repo ekosistem yang memakainya masih aktif di-push, tetapi TIDAK ada bukti independen bahwa ia "proven working" khusus di accounts.x.ai pada 2026. Bukti terbaru justru menunjukkan tanda retak (issue kegagalan, xAI ban domain email).

Yang benar-benar menentukan lolos/tidaknya Turnstile di 2026 bukan extension — tapi **konsistensi fingerprint + IP residential + menjalankan Chrome real secara headed (xvfb), bukan headless**.

---

## 2. Akar Teknis (kenapa turnstilePatch ada, kenapa sudah mati)

### 2a. Apa yang di-patch extension ini
Ketika klik diinjeksi via CDP `Input.dispatchMouseEvent`, browser menghasilkan `MouseEvent` dengan `screenX/screenY` yang **identik dengan `clientX/clientY`** (bug Chromium **#40280325**, ada sejak Chrome 116/2023). Cloudflare Turnstile memakai ini sebagai sinyal bot: pada iframe cross-domain, click CDP memberi `screenY < 100` padahal click manusia memberi nilai ratusan (relatif ke main frame). extension mem-patch `MouseEvent.prototype.screenX/screenY` ke nilai random 800–1200/400–600 di `world:MAIN`, `document_start`, `all_frames:true` supaya nilai "cilik" itu hilang.
- Penjelasan deteksi + reproduksi: https://webscraper.io/blog/google-patches-100-precise-cloudflare-turnstile-bot-check
- README sumber technik (file & manifest operator identik dengan repo ini): https://github.com/TheFalloutOf76/CDP-bug-MouseEvent-.screenX-.screenY-patcher (juga fork https://github.com/ObjectAscended/...)

### 2b. Bukti bug SUDAH DIPATCH (faktor pembunuh utama)
Issue Chromium **#40280325** ditutup sebagai `Fixed` oleh commit:
- **CL 6917162** — "Fix screen coordinates to avoid automation detection" — **submitted 2025-09-15**, `Cr-Commit-Position: refs/heads/main@{#1515705}`, commit hash `4a0b96d3...`
- Commit message (primer): *"Set PositionInScreen based on the view's actual screen bounds instead of copying PositionInWidget. This prevents a common automation detection fingerprint where screenX/Y and clientX/Y are identical, which can be used by sites to trigger anti-bot policies."*
- Sumber: https://chromium-review.googlesource.com/c/chromium/src/+/6917162 dan https://issues.chromium.org/issues/40280325

Since fix landed **Sept 2025** dan rilis ke stable di milestone ~131/132 (akhir 2025), **setiap Chrome/Chromium 2026 sudah membayar patch ini.** Artinya: di Chrome modern, sinyal `screenX==clientX` yang jadi target turnstilePatch sudah tidak ada lagi — extension menjadi tidak relevan terhadap vektor deteksi aslinya. Google memperbaiki bug ini PERSIS karena Cloudflare mengeksploitasinya, jadi sisi "race" sudah ditutup di level browser.

> Catatan penting: karena bug sudah hilang, fungsi patch di atas justru **menimpa nilai screenX/screenY yang sudah benar** dengan nilai palsu. Meng-override getter pada prototype = anomali JS yang bisa di-fingerprint. Ia tak lagi "menolong", malah menambah noise.

---

## 3. Status Ekosistem / GitHub (2026)

| Repo | Aktif | Bukti bekerja di accounts.x.ai 2026? |
|---|---|---|
| **dzDev37/Auto-sign-up-grok-dezz** | ✅ push terakhir 2026-07-22 (50★) | Set-up & flow lengkap untuk accounts.x.ai, tapi **tanpa bukti benchmark sukses terbaru**. Wajib `channel='chrome'`, `headless=False`, `xvfb-run -a`. |
| **ReinerBRO/grok-register** (fork *kevinr229*; DrissionPage) | push 2026-03-19 (395★) | **Indikasi retak**: issue terbuka Maret 2026 — "获取验证码失败" (gagal OTP), "xAI ban duckmail emails". Menandakan akun/email & alur tertekan, bukan turnstile murni. |
| **ObjectAscended / TheFalloutOf76** (sumber teknik) | push 2025-07-10 (558★) | README: "status — Gets past Cloudflare Turnstile". Tapi **predates fix Chrome; tidak di-update untuk 2026.** |

Sumber primer dipakai riset ini: 
- https://github.com/dzDev37/Auto-sign-up-grok-dezz/blob/main/grok-signup.py (baris 177–183: `launch_persistent_context(channel='chrome', headless=False, args=['--load-extension=…','--disable-blink-features=AutomationControlled'], ignore_default_args=['--enable-automation'])`)
- https://github.com/ReinerBRO/grok-register

**Tidak ditemukan** issue/thread (GitHub, linux.do, reddit) yang secara eksplisit dan baru (Q2–Q3 2026) menegaskan turnstilePatch "masih lolos accounts.x.ai". linux.do topic #428459 & #1010988 masih membahas teknis yang sama, tapi konten halaman di belakang CF challenge dan tanpa konfirmasi terbaru-soal-x.ai.

---

## 4. Mengapa kadang MASIH jalan (dan kenapa itu menipu)

1. **Turnstile di beberapa situs mode lenient**: token tidak selalu meng-encode fingerprint penuh. Di mode managed/checkbox yang longgar, browser apa pun yang "cukup wajar" lolos tanpa perlu patch. Jadi lolos ≠ extension bekerja; lolos = sinyal lain (IP, UA, headed) sudah cukup.
2. **Dipasangkan channel='chrome' + xvfb (headed) + residential proxy** — ini faktor dominan. `channel='chrome'` memberi fingerprint TLS/profile Chrome real (bukan Chromium patch build), `headless=False` menghindari deretan sinyal headless yang dibocorkan.
3. Di situs strict 2026, solusi token dari service solver (2captcha/CapSolver) justru FAIL karena token meng-encode fingerprint; token-ke-inject tidak valid. Sumber: https://humanbrowser.cloud/blog/cloudflare-turnstile-bypass-2026
4. Benchmark independen 2026: beberapa tool stealth gagal pada satu halaman Turnstile yang sama sementara **Chrome real via CDP murni (nodriver) LOLOS**; `channel='chrome'` dinilai lebih berpengaruh ketimbang patch itu sendiri. Sumber: https://ianlpaterson.com/blog/anti-detect-browser-benchmark-patchright-nodriver-curl-cffi/

---

## 5. Alternatif FREE yang bekerja 2026 (untuk accounts.x.ai)

| Opsi | Cara kerja | Reliabilitas | Risiko ban |
|---|---|---|---|
| **nodriver** (CDP murni, no WebDriver) | Driver langsung via CDP, patch leak level driver; pakai **Chrome system real** | Tinggi (lolos di benchmark) | Rendah–sedang |
| **patchright** `channel='chrome'` | Fork Playwright, stealth di driver; hidden-patch | Tinggi (benchmark) | Sedang |
| **SeleniumBase UC Mode** | Anti-detect click via CDP, uc_gui click | Tinggi untuk Turnstile | Sedang |
| **Camoufox** (Firefox fork, C++-level) | Fingerprint ulang di level binary | Tertinggi utk fingerprint keras; lambat | Sedang |
| **DrissionPage + vektor CDP** | framework yang sudah meng-embed patch (basis dzDev37/Reiner) | Menengah (terbukti via repo, TAPI stale) | Menengah |

**Pola yang sama-sama ditekankan 2026** (scrapfly, roundproxies, scrapingbee): lolos Turnstile = **residential IP reputation + konsistensi UA/IP/fingerprint + TLS nyata + headed**. Extension patch JS-level bukan lagi pembeda. (https://scrapfly.io/blog/posts/how-to-bypass-cloudflare-turnstile)

---

## 6. Rekomendasi

1. **Jangan andalkan turnstilePatch sebagai mekanisme solusi.** Pertahankan boikot: IP residential non-EU per browser, `channel='chrome'`, `headless=False` + xvfb.
2. **Evaluasi pindah ke nodriver / patchright `channel='chrome'`** yang menerapkan stealth di level driver dan mendapat hasil terbaik di benchmark 2026; extension MV3 bisa dihapus (atau dijadikan fallback belaka).
3. **Hapus/over-ride skill & audit lama** yang diarahkan pada "*turnstilePatch extension = auto-solve*" — dasar teknisnya sudah tidak berlaku.
4. Verifikasi empiris ulang di `accounts.x.ai` dengan 2 varian (dengan & tanpa extension) sebelum cutover.

---

## 7. Sumber (URL)

- https://webscraper.io/blog/google-patches-100-precise-cloudflare-turnstile-bot-check
- https://issues.chromium.org/issues/40280325
- https://chromium-review.googlesource.com/c/chromium/src/+/6917162
- https://github.com/TheFalloutOf76/CDP-bug-MouseEvent-.screenX-.screenY-patcher
- https://github.com/ObjectAscended/CDP-bug-MouseEvent-.screenX-.screenY-patcher
- https://github.com/dzDev37/Auto-sign-up-grok-dezz
- https://github.com/ReinerBRO/grok-register
- https://github.com/topics/cloudflare-turnstile-bypass
- https://ianlpaterson.com/blog/anti-detect-browser-benchmark-patchright-nodriver-curl-cffi/
- https://humanbrowser.cloud/blog/cloudflare-turnstile-bypass-2026
- https://scrapfly.io/blog/posts/how-to-bypass-cloudflare-turnstile
- https://github.com/Kaliiiiiiiiii-Vinyzu/patchright
- https://github.com/ultrafunkamsterdam/nodriver
- https://github.com/daijro/camoufox
