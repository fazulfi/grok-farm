#!/usr/bin/env python3
"""debug_otp.py — diagnosa kenapa read_otp_from_imap gak nemu OTP."""
import imaplib, re, os, sys
from email import message_from_bytes

m = imaplib.IMAP4_SSL("imap.gmail.com", 993, timeout=20)
m.login(os.environ.get("GROK_IMAP_USER", ""), os.environ.get("GROK_IMAP_PASS", "").replace(" ", ""))
m.select("INBOX")
target_local = "lc3ss3un2u8m"
target_email = f"{target_local}@YOURDOMAIN.com"

st, msgs = m.search(None, '(FROM "x.ai")')
mids = [x.decode() for x in (msgs[0].split() if msgs and msgs[0] else [])]
print("count FROM x.ai:", len(mids), mids)
if not mids:
    st, msgs = m.search(None, '(SUBJECT "confirmation code")')
    mids = [x.decode() for x in (msgs[0].split() if msgs and msgs[0] else [])]
    print("fallback subject count:", len(mids), mids)

for mid in mids:
    st, data = m.fetch(mid.encode(), "(RFC822)")
    if not data or not data[0]:
        continue
    msg = message_from_bytes(data[0][1])
    subj = msg.get("Subject", "") or ""
    to_all = " ".join(filter(None, [
        msg.get("To", ""), msg.get("Delivered-To", ""),
        msg.get("X-Original-To", ""), msg.get("X-Forwarded-To", ""),
        msg.get("Envelope-To", ""), msg.get("Received", ""),
    ])).lower()
    print("== msg", mid, "==")
    print(" subj:", subj)
    print(" target_email in to_all:", target_email in to_all)
    print(" target_local in to_all:", target_local in to_all)
    g1 = re.search(r"([A-Z0-9]{3}-[A-Z0-9]{3})", subj)
    g2 = re.search(r"([A-Z0-9]{6})", subj)
    print(" regex subj 3-3:", g1.group(1) if g1 else None)
    print(" regex subj 6:", g2.group(1) if g2 else None)
    print(" to_all[:150]:", to_all[:150])
m.logout()
