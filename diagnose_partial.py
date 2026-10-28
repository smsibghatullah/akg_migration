#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Migrate hui partial reconciles ko source se compare karo (read-only).

  python3 diagnose_partial.py            # pehle 5 migrate hue partials
  python3 diagnose_partial.py 3743       # ek source partial id
"""
import sys
import migrate_transactions as mt

CTX = {"active_test": False}
PF = ["amount", "debit_amount_currency", "credit_amount_currency", "debit_move_id", "credit_move_id",
      "full_reconcile_id", "max_date"]
LF = ["debit", "credit", "amount_residual", "reconciled", "matching_number", "full_reconcile_id", "move_id"]


def pick(conn, model, wanted):
    f = conn.fields(model) or {}
    return [x for x in wanted if x in f]


def line(conn, lid):
    r = conn.call("account.move.line", "read", [lid], fields=pick(conn, "account.move.line", LF), context=CTX)[0]
    return {k: v for k, v in r.items() if k != "id"}


def main():
    pm = "account.partial.reconcile"
    mt.src, mt.dst, mt.MAP = mt.Src(), mt.Dst(), mt.IdMap()
    ids = [int(x) for x in sys.argv[1:]] or [p[0] for p in mt.MAP.tx_src_ids(pm)[:5]]
    print("source partial ids: %s" % ids)
    for sid in ids:
        did = mt.MAP.get(pm, sid)
        s = mt.src.call(pm, "read", [sid], fields=pick(mt.src, pm, PF), context=CTX)[0]
        print("\n=== source partial #%s -> dst #%s" % (sid, did))
        print("SRC: %s" % {k: v for k, v in s.items() if k != "id"})
        if not did:
            continue
        d = mt.dst.call(pm, "read", [did], fields=pick(mt.dst, pm, PF), context=CTX)[0]
        print("DST: %s" % {k: v for k, v in d.items() if k != "id"})
        for side in ("debit_move_id", "credit_move_id"):
            sl = mt.m2o_id(s[side])
            dl = mt.resolve("account.move.line", sl)
            print("  %s  SRC line #%s: %s" % (side, sl, line(mt.src, sl)))
            print("  %s  DST line #%s: %s" % (side, dl, line(mt.dst, dl) if dl else "map nahi"))


if __name__ == "__main__":
    main()
