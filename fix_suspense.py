import csv, sys, collections
import migrate_transactions as mt
mt.src, mt.dst = mt.Src(), mt.Dst()
C = {"active_test": False}
CTX = getattr(mt, "CTX_MOVE", C)
apply = "--apply" in sys.argv
limit = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else 0
SUS = {"101401", "105220"}

pairs = sorted({(int(r["src_move"]), int(r["dst_move"])) for r in csv.DictReader(open("bs_bad_moves.csv"))})
code = lambda r: r["account_id"][1].split(" ")[0] if r["account_id"] else "-"
acc_cache = {}
def acc_id(c, side, cd):
    k = (side, cd)
    if k not in acc_cache:
        r = c.call("account.account", "search_read", [["code", "=", cd]], fields=["id"], context=C)
        acc_cache[k] = r[0]["id"] if r else None
    return acc_cache[k]

stats = collections.Counter(); n = 0
for s, d in pairs:
    S = mt.src.call("account.move.line", "search_read", [["move_id", "=", s]],
                    fields=["account_id", "balance", "amount_currency", "currency_id"], order="id", context=C)
    D = mt.dst.call("account.move.line", "search_read", [["move_id", "=", d]],
                    fields=["account_id", "balance", "amount_currency", "currency_id"], order="id", context=C)
    sus = [l for l in D if code(l) in SUS]
    if len(D) != 2 or len(sus) != 1:
        stats["skip: dst shape"] += 1; continue
    cand = [l for l in S if abs(l["balance"] - sus[0]["balance"]) < 0.01 and not code(l).startswith("105")]
    if len(cand) != 1:
        stats["skip: src match"] += 1; continue
    tgt = acc_id(mt.dst, "d", code(cand[0]))
    if not tgt:
        stats["skip: account nahi"] += 1; continue
    vals = {"account_id": tgt}
    if cand[0]["currency_id"] and abs(cand[0]["amount_currency"] - sus[0]["amount_currency"]) > 0.001:
        vals["amount_currency"] = cand[0]["amount_currency"]
    stats["ok " + code(cand[0])] += 1
    print("dst move", d, "line", sus[0]["id"], "->", code(cand[0]), vals)
    if apply:
        try:
            mt.dst.call("account.move.line", "write", [sus[0]["id"]], vals, context=CTX)
        except Exception as e:
            stats["FAIL"] += 1; print("   FAIL:", str(e)[:200].replace("\n", " "))
    n += 1
    if limit and n >= limit: break
print(dict(stats), "(dry-run)" if not apply else "")
