import csv, re, sys
import migrate_transactions as mt
mt.src, mt.dst = mt.Src(), mt.Dst()
C = {"active_test": False}
CTX = getattr(mt, "CTX_MOVE", C)
apply = "--apply" in sys.argv
d2s = {int(r["dst_move"]): int(r["src_move"]) for r in csv.DictReader(open("bs_bad_moves.csv"))}
code = lambda l: l["account_id"][1].split(" ")[0]
import itertools
moves = sorted({int(m) for m in re.findall(r"^dst move (\d+) line", open("fix_suspense.log").read(), re.M)}
               | {15048, 28997, 29015, 29097, 34656})
F = ["account_id", "debit", "credit", "balance", "amount_currency"]
LIM = int(sys.argv[sys.argv.index('--limit')+1]) if '--limit' in sys.argv else 0
for d in (moves[:LIM] if LIM else moves):
    s = d2s.get(d)
    S = mt.src.call("account.move.line", "search_read", [["move_id", "=", s]], fields=F, order="id", context=C)
    D = mt.dst.call("account.move.line", "search_read", [["move_id", "=", d]], fields=F, order="id", context=C)
    tot = round(sum(l["balance"] for l in D), 3)
    if abs(tot) < 0.01:
        print(d, "already balanced"); continue
    for l in D:
        if code(l) in ("101200", "201000", "101100"):
            c = [x for x in S if code(x) == code(l) and abs(x["balance"]) > 0.01]
            if len(c) != 1:
                print(d, "SKIP: src match", len(c)); break
            v = {"debit": c[0]["debit"], "credit": c[0]["credit"], "amount_currency": c[0]["amount_currency"]}
            print(d, "line", l["id"], l["balance"], "->", v)
            if apply:
                mt.dst.call("account.move.line", "write", [l["id"]], v, context=CTX)
    if apply:
        D = mt.dst.call("account.move.line", "search_read", [["move_id", "=", d]], fields=F, context=C)
        print("   sum after:", round(sum(x["balance"] for x in D), 3))
