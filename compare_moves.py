import collections
import migrate_transactions as mt
mt.src, mt.dst, mt.MAP = mt.Src(), mt.Dst(), mt.IdMap()
C = {"active_test": False}
db = mt.MAP.db
cols = [r[1] for r in db.execute("pragma table_info(m)")]
sc = [c for c in cols if c not in ("model", "dst")][0]
pairs = {int(s): int(d) for s, d in db.execute("select %s, dst from m where model=?" % sc, ("account.move",))}

def load(c, label):
    agg = {}   # move_id -> [n, debit, credit, cogs_net]
    last = 0
    while True:
        rows = c.call("account.move.line", "search_read", [["id", ">", last]],
                      fields=["move_id", "account_id", "debit", "credit"],
                      limit=20000, order="id", context=C)
        if not rows:
            break
        for r in rows:
            last = max(last, r["id"])
            if not r["move_id"]:
                continue
            a = agg.setdefault(r["move_id"][0], [0, 0.0, 0.0, 0.0])
            a[0] += 1; a[1] += r["debit"]; a[2] += r["credit"]
            nm = r["account_id"][1] if r["account_id"] else ""
            if nm.startswith("63"):
                a[3] += r["debit"] - r["credit"]
        print(label, last)
    return agg

S, D = load(mt.src, "SRC"), load(mt.dst, "DST")
more_lines, diff_cogs, tot = 0, 0, 0.0
rows = []
for s, d in pairs.items():
    a, b = S.get(s), D.get(d)
    if not a or not b:
        continue
    if b[0] != a[0]:
        more_lines += 1
    cd = round(b[3] - a[3], 2)
    if abs(cd) > 0.01:
        diff_cogs += 1; tot += cd
        rows.append((abs(cd), s, d, a[0], b[0], cd))
print("\nmoves jin mein line count alag:", more_lines)
print("moves jin mein COGS (63xxxx) alag:", diff_cogs, "| total COGS farq (dst - src):", round(tot, 2))
rows.sort(reverse=True)
names = {r["id"]: r["name"] for r in mt.dst.call("account.move", "read", [r[2] for r in rows[:15]],
                                                  fields=["name"], context=C)} if rows else {}
for r in rows[:15]:
    print("src", r[1], "dst", r[2], names.get(r[2]), "| lines src/dst:", r[3], "/", r[4], "| cogs diff:", r[5])
