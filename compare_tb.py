import csv, collections
import migrate_transactions as mt
mt.src, mt.dst = mt.Src(), mt.Dst()
C = {"active_test": False}
F = ["account_id", "debit", "credit", "date", "parent_state"]

def load(c, label):
    tb = collections.defaultdict(lambda: [0.0, 0.0])
    st = collections.Counter()
    name = {}
    last, n = 0, 0
    while True:
        rows = c.call("account.move.line", "search_read", [["id", ">", last]],
                      fields=F, limit=20000, order="id", context=C)
        if not rows:
            break
        for r in rows:
            last = max(last, r["id"]); n += 1
            st[r["parent_state"]] += 1
            if r["parent_state"] != "posted" or not r["account_id"]:
                continue
            code = r["account_id"][1].split(" ")[0]
            name[code] = r["account_id"][1]
            k = (code, (r["date"] or "")[:4])
            tb[k][0] += r["debit"]; tb[k][1] += r["credit"]
        print(label, n, "lines...")
    print(label, "states:", dict(st))
    return tb, name

s, sn = load(mt.src, "SRC")
d, dn = load(mt.dst, "DST")
rows, bad = [], 0
for k in sorted(set(s) | set(d)):
    a, b = s.get(k, [0, 0]), d.get(k, [0, 0])
    diff = round((a[0] - a[1]) - (b[0] - b[1]), 2)
    if abs(diff) > 0.01:
        bad += 1
    rows.append([k[0], k[1], (sn.get(k[0]) or dn.get(k[0]) or "")[:40],
                 round(a[0] - a[1], 2), round(b[0] - b[1], 2), diff])
with open("tb_compare.csv", "w", newline="") as f:
    w = csv.writer(f); w.writerow(["code", "year", "name", "src_balance", "dst_balance", "diff"]); w.writerows(rows)
print("\nFarq wali rows:", bad, "/", len(rows))
for r in sorted([r for r in rows if abs(r[5]) > 0.01], key=lambda r: -abs(r[5]))[:25]:
    print(r)
