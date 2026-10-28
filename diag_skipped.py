import collections
import migrate_transactions as mt
mt.src, mt.dst = mt.Src(), mt.Dst()
C = {"active_test": False}
pairs = [tuple(map(int, l.split(","))) for l in open("fx_bad_moves.txt") if l.strip()]
F = ["move_id", "account_id", "debit", "credit"]
def lines(c, ids):
    out = {}
    for r in c.call("account.move.line", "search_read", [["move_id", "in", ids]], fields=F, order="id", context=C):
        out.setdefault(r["move_id"][0], []).append(r)
    return out
code = lambda r: (r["account_id"][1].split(" ")[0] if r["account_id"] else "-")
reasons, acc = collections.Counter(), collections.Counter()
rows = []
for i in range(0, len(pairs), 200):
    ch = pairs[i:i+200]
    S, D = lines(mt.src, [p[0] for p in ch]), lines(mt.dst, [p[1] for p in ch])
    for s, d in ch:
        a, b = S.get(s, []), D.get(d, [])
        if not a or len(a) != len(b):
            why = "line count %d/%d" % (len(a), len(b))
        elif any(code(x) != code(y) for x, y in zip(a, b)):
            why = "account alag"
        else:
            continue
        reasons[why.split(" ")[0] + " " + why.split(" ")[1]] += 1
        for r in a: acc[code(r)] += r["debit"] - r["credit"]
        for r in b: acc[code(r)] -= r["debit"] - r["credit"]
        rows.append((s, d, why))
print("skipped:", len(rows), dict(reasons))
print("in moves ka account-wise farq (src - dst), bade 10:")
for k, v in sorted(acc.items(), key=lambda x: -abs(x[1]))[:10]: print("  ", k, round(v, 2))
for r in rows[:10]: print(r)
