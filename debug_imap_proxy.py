#!/usr/bin/env python3
"""debug_imap_proxy.py — tes koneksi IMAP via proxy step by step."""
import sys
sys.path.insert(0, "/home/grok/grok-farm")
import farm_v2

# load proxy
farm_v2.PROXY_POOL = farm_v2.load_proxy_pool()
px = farm_v2.next_proxy()
print("proxy:", px, flush=True)
try:
    mail = farm_v2._imap_connect(px)
    print("CONNECT OK", flush=True)
    try:
        r = mail.login(farm_v2.IMAP_USER, farm_v2.IMAP_PASS)
        print("LOGIN OK:", r, flush=True)
        st, data = mail.select("INBOX")
        print("SELECT:", st, data[0] if data else None, flush=True)
        mail.logout()
    except Exception as e:
        print("LOGIN FAIL:", type(e).__name__, str(e)[:200], flush=True)
except Exception as e:
    print("CONNECT FAIL:", type(e).__name__, str(e)[:200], flush=True)
