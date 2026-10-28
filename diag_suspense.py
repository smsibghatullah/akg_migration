import collections
import migrate_transactions as mt
mt.src, mt.dst, mt.MAP = mt.Src(), mt.Dst(), mt.IdMap()
C = {"active_test": False}
db = mt.MAP.db
cols = [r[1] for r in db.execute("pragma table_info(m)")]
sc = [c for c in cols if c not in ("model", "dst")][0]
pairs = {int(d): int(s) for s, d in db.execute("select %s, dst from m where model=?" % sc, ("account.move",))}

sus = mt.dst.call("account.move.line", "search_read",
                  [["account_id.code", "like", "1014%"], ["parent_state", "=", "posted"]],
                  fields=["move_id", "account_id", "debit", "credit"], context=C)
sus += mt.dst.call("account.move.line", "search_read",
                   [["account_id.code", "=", "105220"], ["parent_state", "=", "posted"]],
                   fields=["move_id", "account_id", "debit", "credit"], context=C)
print("destination suspense lines:", len(sus))
tgt = collections.Counter(); unmapped = 0; sample = []
for l in sus:
    s = pairs.get(l["move_id"][0])
    if not s:
        unmapped += 1; continue
    src = mt.src.call("account.move.line", "search_read", [["move_id", "=", s]],
                      fields=["account_id", "debit", "credit", "reconciled"], context=C)
    cand = [x for x in src if abs((x["debit"] - x["credit"]) - (l["debit"] - l["credit"])) < 0.01
            and not x["account_id"][1].startswith("105")]
    k = cand[0]["account_id"][1][:30] if cand else "NO MATCH"
    tgt[k] += 1
    if len(sample) < 5: sample.append((l["move_id"][1], l["account_id"][1][:25], "->", k))
print("unmapped moves:", unmapped)
print("source ke hisaab se target account:")
for k, v in tgt.most_common(): print("  ", v, k)
for s in sample: print(s)
