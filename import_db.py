import sqlite3, os, glob, sys

DB = os.path.expanduser("~/grok-farm/akun.db")
conn = sqlite3.connect(DB)

conn.executescript("""
    CREATE TABLE IF NOT EXISTS accounts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        access_token TEXT,
        refresh_token TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        batch_id TEXT,
        proxy_used TEXT,
        status TEXT DEFAULT 'farmed',
        injected_at TIMESTAMP,
        grok_cli_connection_id TEXT,
        ninerouter_name TEXT,
        notes TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_status ON accounts(status);
    CREATE INDEX IF NOT EXISTS idx_email ON accounts(email);
""")

imported = 0
for bdir in sorted(glob.glob(os.path.expanduser("~/grok-farm/results/batch_*"))):
    acct_file = os.path.join(bdir, "accounts.txt")
    if not os.path.exists(acct_file):
        continue
    batch_id = os.path.basename(bdir)
    for line in open(acct_file):
        parts = line.strip().split("|")
        if len(parts) < 1:
            continue
        email = parts[0]
        password = parts[1] if len(parts) > 1 else ""
        at = parts[2] if len(parts) > 2 else ""
        rt = parts[3] if len(parts) > 3 else ""
        try:
            conn.execute("INSERT INTO accounts (email,password,access_token,refresh_token,batch_id,status) VALUES (?,?,?,?,?,?)",
                        (email, password, at, rt, batch_id, "farmed"))
            imported += 1
        except sqlite3.IntegrityError:
            pass

conn.commit()
total = conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
conn.close()
if imported > 0:
    print(f"[DB] Imported {imported} new accounts | Total: {total}")
