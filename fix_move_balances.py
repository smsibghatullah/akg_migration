import sys
import migrate_transactions as mt
mt.src, mt.dst = mt.Src(), mt.Dst()
C = {"active_test": False}
CTX = getattr(mt, "CTX_MOVE", C)
apply = "--apply" in sys.argv
limit = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else 0
pairs = [tuple(map(int, l.split(","))) for l in open("fx_bad_moves.txt") if l.strip()]
F = ["move_id", "account_id", "debit", "credit", "amount_currency"]

def lines(c, ids):
    out = {}
    rows = c.call("account.move.line", "search_read", [["move_id", "in", ids]],
                  fields=F, order="id", context=C)
    for r in rows:
        out.setdefault(r["move_id"][0], []).append(r)
    return out

stats = {"moves": 0, "lines_fixed": 0, "skip": 0, "fail": 0}
done = 0
for i in range(0, len(pairs), 200):
    chunk = pairs[i:i + 200]
    S = lines(mt.src, [p[0] for p in chunk])
    D = lines(mt.dst, [p[1] for p in chunk])
    for s, d in chunk:
        a, b = S.get(s, []), D.get(d, [])
        if len(a) != len(b) or not a:
            stats["skip"] += 1; continue
        if any((x["account_id"] or [0, ""])[1].split(" ")[0] != (y["account_id"] or [0, ""])[1].split(" ")[0]
               for x, y in zip(a, b)):
            stats["skip"] += 1; continue
        todo = [(y["id"], {"debit": x["debit"], "credit": x["credit"], "amount_currency": x["amount_currency"]})
                for x, y in zip(a, b)
                if abs(x["debit"] - y["debit"]) > 0.5 or abs(x["credit"] - y["credit"]) > 0.5
                or abs(x["amount_currency"] - y["amount_currency"]) > 0.5]
        if not todo:
            continue
        stats["moves"] += 1
        print("move src %s dst %s: %d lines" % (s, d, len(todo)))
        if apply:
            try:
                # sab lines ek hi write mein nahi ja sakti (alag values), is liye ek ek karke
                for lid, vals in todo:
                    mt.dst.call("account.move.line", "write", [lid], vals, context=CTX)
                stats["lines_fixed"] += len(todo)
            except Exception as e:
                stats["fail"] += 1
                print("   FAIL:", str(e)[:200].replace("\n", " "))
        done += 1
        if limit and done >= limit:
            break
    if limit and done >= limit:
        break
print(stats, "(dry-run)" if not apply else "")
