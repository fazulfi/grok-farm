#!/usr/bin/env python3
"""debug_last_otp.py — cek email xAI terbaru di inbox (via proxy)."""
import sys
sys.path.insert(0, "/home/grok/grok-farm")
import farm_v2

farm_v2.PROXY_POOL = farm_v2.load_proxy_pool()
px = farm_v2.next_proxy()
print("PROXY:", (px or "none")[:50], flush=True)
try:
    mail = farm_v2._imap_connect(px)
    print("CONNECT OK", flush=True)
    mail.login(farm_v2.IMAP_USER, farm_v2.IMAP_PASS)
    mail.select("INBOX")
    st, msgs = mail.search(None, '(FROM "x.ai")')
    mids = [x.decode() for x in (msgs[0].split() if msgs and msgs[0] else [])]
    print("x.ai msgs:", len(mids), flush=True)
    for mid in mids[-5:]:
        st, data = mail.fetch(mid.encode(), "(BODY.PEEK[HEADER.FIELDS (SUBJECT TO DELIVERED-TO DATE)])")
        if data and data[0]:
            hdr = data[0][1].decode(errors="replace").replace("\r\n", " | ")
            print("  MSG:", hdr[:180], flush=True)
    mail.logout()
except Exception as e:
    print("FAIL:", type(e).__name__, str(e)[:200], flush=True)
