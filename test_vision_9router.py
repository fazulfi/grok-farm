#!/usr/bin/env python3
"""test_vision_9router.py — tes vision model via 9router (screenshot png → deskripsi)."""
import base64, json, sys, urllib.request

KEY = sys.argv[1] if len(sys.argv) > 1 else ""
IMG = sys.argv[2] if len(sys.argv) > 2 else ""
MODEL = sys.argv[3] if len(sys.argv) > 3 else "openrouter/openai/gpt-4o"
if not KEY or not IMG:
    print("usage: test_vision_9router.py <key> <img> [model]"); sys.exit(1)

with open(IMG, "rb") as f:
    b64 = base64.b64encode(f.read()).decode()
payload = {
    "model": MODEL,
    "messages": [{"role": "user", "content": [
        {"type": "text", "text": "Describe what you see in this image in 1 sentence."},
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
    ]}],
    "max_tokens": 200,
}
req = urllib.request.Request(
    "https://router.YOURDOMAIN.com/v1/chat/completions",
    data=json.dumps(payload).encode(),
    headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"},
    method="POST",
)
try:
    with urllib.request.urlopen(req, timeout=90) as r:
        d = json.loads(r.read().decode())
        print("MODEL:", d.get("model"))
        print("RESP:", d.get("choices", [{}])[0].get("message", {}).get("content", "")[:300])
except urllib.error.HTTPError as e:
    print("HTTP", e.code, e.read().decode()[:500])
except Exception as e:
    print("ERR", e)
