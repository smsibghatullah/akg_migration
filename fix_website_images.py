import migrate_transactions as mt
mt.src, mt.dst, mt.MAP = mt.Src(), mt.Dst(), mt.IdMap()
C = {"active_test": False}
db = mt.MAP.db
cols = [r[1] for r in db.execute("pragma table_info(m)")]
sc = [c for c in cols if c not in ("model", "dst")][0]
rows = db.execute("select %s, dst from m where model=?" % sc, ("website.attachment",)).fetchall()
print("columns:", cols, "| attachments:", len(rows))
for sid, did in rows:
    r = mt.dst.call("ir.attachment", "read", [int(did)], fields=["file_size", "name"], context=C)
    if not r:
        print("dst", did, "nahi mila"); continue
    if r[0]["file_size"]:
        print("OK already", did, r[0]["name"]); continue
    s = mt.src.call("ir.attachment", "read", [int(sid)], fields=["datas"], context=C)
    if not s or not s[0].get("datas"):
        print("source khali", sid); continue
    mt.dst.call("ir.attachment", "write", [int(did)], {"raw": s[0]["datas"]}, context=C)
    r = mt.dst.call("ir.attachment", "read", [int(did)], fields=["file_size"], context=C)
    print("filled", sid, "->", did, r[0]["file_size"])
