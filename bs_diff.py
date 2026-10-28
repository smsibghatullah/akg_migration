import collections, csv
import migrate_transactions as mt
mt.src, mt.dst, mt.MAP = mt.Src(), mt.Dst(), mt.IdMap()
C = {"active_test": False}
CUT = "2024-12-31"
db = mt.MAP.db
cols = [r[1] for r in db.execute("pragma table_info(m)")]
sc = [c for c in cols if c not in ("model", "dst")][0]
pairs = {int(s): int(d) for s, d in db.execute("select %s, dst from m where model=?" % sc, ("account.move",))}
inv = {d: s for s, d in pairs.items()}

def load(c, label):
    agg, last = collections.defaultdict(lambda: collections.defaultdict(float)), 0
    while True:
        rows = c.call("account.move.line", "search_read",
                      [["id", ">", last], ["parent_state", "=", "posted"], ["date", "<=", CUT]],
                      fields=["move_id", "account_id", "balance"], limit=20000, order="id", context=C)
        if not rows: break
        for r in rows:
            last = max(last, r["id"])
            code = r["account_id"][1].split(" ")[0] if r["account_id"] else "-"
            agg[r["move_id"][0]][code] += r["balance"]
        print(label, last)
    return agg

S, D = load(mt.src, "SRC"), load(mt.dst, "DST")
cause = collections.defaultdict(lambda: collections.defaultdict(float))
bad, seen = [], set()
for s, acc in S.items():
    d = pairs.get(s)
    if d is None:
        for k, v in acc.items(): cause["A"][k] += v
        continue
    seen.add(d); dd = D.get(d, {})
    for k in set(acc) | set(dd):
        diff = acc.get(k, 0) - dd.get(k, 0)
        if abs(diff) < 0.0005: continue
        if abs(diff) <= 0.02: cause["C"][k] += diff
        else:
            cause["B"][k] += diff; bad.append((s, d, k, round(diff, 3)))
for d, acc in D.items():
    if d in seen: continue
    for k, v in acc.items():
        if d in inv:
            cause["B"][k] -= v; bad.append((inv[d], d, k, round(-v, 3)))
        else:
            cause["D"][k] -= v
tot = collections.defaultdict(float)
for m in cause.values():
    for k, v in m.items(): tot[k] += v
print("\nAccount | total farq (src-dst) | A=migrate nahi B=amount/account alag C=rounding D=dst extra")
for k, v in sorted(tot.items(), key=lambda x: -abs(x[1])):
    if abs(v) < 1: continue
    print("%-8s %12.3f | %s" % (k, v, " ".join("%s=%.1f" % (c, cause[c][k]) for c in "ABCD" if abs(cause[c].get(k, 0)) >= 0.5)))
with open("bs_bad_moves.csv", "w", newline="") as f:
    w = csv.writer(f); w.writerow(["src_move", "dst_move", "account", "diff"]); w.writerows(bad)
print("B wale moves:", len(set((b[0]) for b in bad)), "-> bs_bad_moves.csv")
