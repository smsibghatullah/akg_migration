import csv, re, sys
import migrate_transactions as mt
mt.src, mt.dst = mt.Src(), mt.Dst()
C = {"active_test": False}
CTX = getattr(mt, "CTX_MOVE", C)
apply = "--apply" in sys.argv
LIM = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else 0
d2s = {int(r["dst_move"]): int(r["src_move"]) for r in csv.DictReader(open("bs_bad_moves.csv"))}
code = lambda l: l["account_id"][1].split(" ")[0]
moves = sorted({int(m) for m in re.findall(r"^dst move (\d+) line", open("fix_suspense.log").read(), re.M)}
               | {15048, 28997, 29015, 29097, 34656})
F = ["account_id", "debit", "credit", "balance", "amount_currency", "currency_id"]
cur = {}
def dst_cur(name):
    if name not in cur:
        r = mt.dst.call("res.currency", "search_read", [["name", "=", name]], fields=["id"], context=C)
        cur[name] = r[0]["id"]
    return cur[name]
n = 0
for d in moves:
    S = mt.src.call("account.move.line", "search_read", [["move_id", "=", d2s.get(d)]], fields=F, order="id", context=C)
    D = mt.dst.call("account.move.line", "search_read", [["move_id", "=", d]], fields=F, order="id", context=C)
    if abs(sum(l["balance"] for l in D)) < 0.01:
        continue
    for l in D:
        if code(l) not in ("101200", "201000", "101100"):
            continue
        c = [x for x in S if code(x) == code(l) and abs(x["balance"]) > 0.01]
        if len(c) != 1:
            print(d, "SKIP src match", len(c)); break
        c = c[0]
        v = {"debit": c["debit"], "credit": c["credit"], "amount_currency": c["amount_currency"]}
        print(d, "line", l["id"], "currency", c["currency_id"][1], "->", v)
        if apply:
            mt.dst.call("account.move.line", "write", [l["id"]], {"currency_id": dst_cur(c["currency_id"][1])}, context=CTX)
            mt.dst.call("account.move.line", "write", [l["id"]], v, context=CTX)
    if apply:
        D = mt.dst.call("account.move.line", "search_read", [["move_id", "=", d]], fields=F, context=C)
        print("   sum after:", round(sum(x["balance"] for x in D), 3),
              [(x["balance"], x["amount_currency"]) for x in D])
    n += 1
    if LIM and n >= LIM:
        break
