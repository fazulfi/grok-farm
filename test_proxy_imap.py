#!/usr/bin/env python3
"""test_proxy_imap.py — tes CONNECT tunnel via WebShare proxy ke imap.gmail.com:993."""
import base64
import socket
import sys


def test(proxy_line: str):
    parts = proxy_line.strip().split(":")
    if len(parts) != 4:
        print("bad proxy line:", proxy_line)
        return
    host, port, user, password = parts
    try:
        s = socket.create_connection((host, int(port)), timeout=12)
        auth = base64.b64encode(f"{user}:{password}".encode()).decode()
        req = (
            f"CONNECT imap.gmail.com:993 HTTP/1.1\r\n"
            f"Host: imap.gmail.com:993\r\n"
            f"Proxy-Authorization: Basic {auth}\r\n"
            f"Proxy-Connection: Keep-Alive\r\n\r\n"
        )
        s.send(req.encode())
        resp = s.recv(300).decode(errors="replace")
        first = resp.split("\r\n")[0] if resp else "(empty)"
        print(f"  [{host}:{port}] {first}")
        if "200" in first:
            print("  -> TUNNEL OK (IMAP 993 reachable via proxy)")
            s.close()
            return True
        s.close()
    except Exception as e:
        print(f"  [{host}:{port}] err: {e}")
    return False


if __name__ == "__main__":
    lines = open("/home/grok/grok-farm/proxies.txt").read().splitlines()
    ok = 0
    for line in lines[:10]:
        if test(line):
            ok += 1
        if ok >= 3:
            break
    print(f"\nTUNNEL OK: {ok}/10 proxy")
    sys.exit(0 if ok else 1)
