import collections
import migrate_transactions as mt
mt.src, mt.dst, mt.MAP = mt.Src(), mt.Dst(), mt.IdMap()
C = {"active_test": False}
db = mt.MAP.db
cols = [r[1] for r in db.execute("pragma table_info(m)")]
sc = [c for c in cols if c not in ("model", "dst")][0]
pairs = {int(s): int(d) for s, d in db.execute("select %s, dst from m where model=?" % sc, ("account.move",))}
inv = {d: s for s, d in pairs.items()}

def load(c, label):
    agg, last = {}, 0
    while True:
        rows = c.call("account.move.line", "search_read", [["id", ">", last]],
                      fields=["move_id", "balance", "amount_currency", "currency_id", "sequence"],
                      limit=20000, order="id", context=C)
        if not rows: break
        for r in rows:
            last = max(last, r["id"])
            if r["move_id"]:
                agg.setdefault(r["move_id"][0], []).append((round(r["balance"], 2), round(r["amount_currency"], 2),
                                                           r["currency_id"][1] if r["currency_id"] else ""))
        print(label, last)
    return agg

S, D = load(mt.src, "SRC"), load(mt.dst, "DST")
bad, by_cur, sample = [], collections.Counter(), []
for s, d in pairs.items():
    a, b = S.get(s), D.get(d)
    if not a or not b or len(a) != len(b): continue
    if sorted(x[0] for x in a) != sorted(x[0] for x in b):
        bad.append((s, d)); by_cur[a[0][2] or "-"] += 1
        if len(sample) < 5: sample.append((s, d, a[:2], b[:2]))
print("\nmoves jin ka balance source se alag:", len(bad), "/", len(pairs))
print("currency wise:", dict(by_cur))
for x in sample: print(x)
open("fx_bad_moves.txt", "w").write("\n".join("%s,%s" % p for p in bad))
