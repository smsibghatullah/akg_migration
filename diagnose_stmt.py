#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Statement line fail kyun hui? Uski move aur reconciliation Odoo 16 vs Odoo 20 (read-only).

  python3 diagnose_stmt.py 163        # 163 = Odoo 16 ka account.bank.statement.line id
"""
import sys
import migrate_transactions as mt

CTX = {"active_test": False}
SL = ["date", "payment_ref", "amount", "partner_id", "journal_id", "move_id", "is_reconciled", "statement_id"]
ML = ["account_id", "debit", "credit", "reconciled", "matching_number", "full_reconcile_id", "amount_residual",
      "partner_id", "payment_id"]
MV = ["name", "state", "move_type", "date", "journal_id", "payment_state", "statement_line_id"]


def pick(conn, model, wanted):
    f = conn.fields(model) or {}
    return [x for x in wanted if x in f]


def show_move(label, conn, mid):
    m = conn.call("account.move", "read", [mid], fields=pick(conn, "account.move", MV), context=CTX)[0]
    print("\n%s move #%s: %s" % (label, mid, {k: v for k, v in m.items() if k != "id"}))
    ids = conn.call("account.move.line", "search", [("move_id", "=", mid)], order="id", context=CTX)
    for l in conn.call("account.move.line", "read", ids, fields=pick(conn, "account.move.line", ML), context=CTX):
        print("   line %-7s %-30s dr=%-10s cr=%-10s reconciled=%s match=%s full=%s residual=%s payment=%s" % (
            l["id"], (l.get("account_id") or ["", ""])[1][:30], l.get("debit"), l.get("credit"), l.get("reconciled"),
            l.get("matching_number"), l.get("full_reconcile_id"), l.get("amount_residual"), l.get("payment_id")))
    return ids


def show_partials(label, conn, ids):
    pm = "account.partial.reconcile"
    rows = conn.call(pm, "search_read", ["|", ("debit_move_id", "in", ids), ("credit_move_id", "in", ids)],
                     fields=pick(conn, pm, ["amount", "debit_amount_currency", "credit_amount_currency",
                                            "debit_move_id", "credit_move_id", "full_reconcile_id", "max_date"]),
                     context=CTX)
    print("   %s partial reconciles is move ki lines par: %d" % (label, len(rows)))
    for r in rows:
        print("      #%s %s" % (r["id"], {k: v for k, v in r.items() if k != "id"}))


def main():
    sid = int(sys.argv[1])
    mt.src, mt.dst, mt.MAP = mt.Src(), mt.Dst(), mt.IdMap()
    m = "account.bank.statement.line"
    s = mt.src.call(m, "read", [sid], fields=pick(mt.src, m, SL), context=CTX)[0]
    print("SRC statement line #%s: %s" % (sid, {k: v for k, v in s.items() if k != "id"}))
    smove = mt.m2o_id(s["move_id"])
    sids = show_move("SRC", mt.src, smove)
    show_partials("SRC", mt.src, sids)
    dmove = mt.resolve("account.move", smove)
    print("\nstatement line ka dst map: %s | move ka dst map: %s" % (mt.MAP.get(m, sid), dmove))
    if dmove:
        dids = show_move("DST", mt.dst, dmove)
        show_partials("DST", mt.dst, dids)
        ex = mt.dst.call(m, "search_read", [("move_id", "=", dmove)], fields=["amount", "is_reconciled"], context=CTX)
        print("\nDST mein is move par statement lines: %s" % ex)


if __name__ == "__main__":
    main()
