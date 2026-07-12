import sqlite3, json
c = sqlite3.connect("/var/lib/9router/db/data.sqlite")
rows = c.execute("SELECT data FROM proxyPools").fetchall()
c.close()
print(f"COUNT={len(rows)}")
for (d,) in rows:
    try:
        o = json.loads(d)
        u = (o.get("proxyUrl") or "").strip().rstrip("/")
        if u:
            print(u)
    except Exception as e:
        print(f"ERR {e}")
