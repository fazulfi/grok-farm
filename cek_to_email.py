#!/usr/bin/env python3
"""cek_to_email.py — cek To: & kode semua email x.ai di inbox (diagnosis OTP invalid)."""
import imaplib, re, sys
sys.path.insert(0, "/home/grok/grok-farm")
from email import message_from_bytes
from email.header import decode_header

import farm_v2

def hdr(v):
    if not v:
        return ""
    out = ""
    for part, enc in decode_header(v):
        if isinstance(part, bytes):
            out += part.decode(enc or "utf-8", errors="replace")
        else:
            out += part
    return out

farm_v2.PROXY_POOL = farm_v2.load_proxy_pool()
px = farm_v2.next_proxy()
m = farm_v2._imap_connect(px)
m.login("adisantososaja676@gmail.com", "hjvifrbobkysznbh")
m.select("INBOX")
st, data = m.search(None, '(FROM "x.ai")')
ids = data[0].split()
print("total x.ai emails:", len(ids))
for i in ids[-6:]:
    st2, msg = m.fetch(i, "(RFC822)")
    em = message_from_bytes(msg[0][1])
    subj = hdr(em.get("Subject"))
    to = hdr(em.get("To"))
    dt = em.get("Date")
    dl = hdr(em.get("Delivered-To"))
    code = re.search(r"([A-Z0-9]{3}-[A-Z0-9]{3})", subj)
    print(f"id={i.decode()} date={dt}")
    print(f"  To: {to}")
    print(f"  Delivered-To: {dl}")
    print(f"  Subj: {subj}  Code: {code.group(1) if code else '??'}")
m.logout()
