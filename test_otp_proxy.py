#!/usr/bin/env python3
"""test_otp_proxy.py — tes read_otp_from_imap via proxy (email OTP yang sudah ada)."""
import sys
sys.path.insert(0, "/home/grok/grok-farm")
import farm_v2

farm_v2.PROXY_POOL = farm_v2.load_proxy_pool()
px = farm_v2.next_proxy()
print("PROXY:", (px or "none")[:60], flush=True)
code = farm_v2.read_otp_from_imap("lc3ss3un2u8m@YOURDOMAIN.com", timeout=40, proxy_url=px)
print("OTP_VIA_PROXY:", code, flush=True)
