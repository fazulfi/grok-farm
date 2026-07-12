import sqlite3
c = sqlite3.connect("/home/magadirxwin/grok-farm/akun.db")
t = c.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
i = c.execute("SELECT COUNT(*) FROM accounts WHERE status='injected'").fetchone()[0]
f = c.execute("SELECT COUNT(*) FROM accounts WHERE status='farmed'").fetchone()[0]
print(f"Total: {t} | Injected: {i} | Farmed: {f}")
print()
for r in c.execute("SELECT id,email,status,batch_id FROM accounts ORDER BY id DESC LIMIT 5"):
    print(f"  [{r[0]}] {r[1]:45s} {r[2]:10s} ({r[3]})")
c.close()
