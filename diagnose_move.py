#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ek migrate hue journal entry / invoice ki lines Odoo 16 vs Odoo 20 side by side.

  python3 diagnose_move.py 67          # 67 = Odoo 16 ka account.move id
"""
import sys
import migrate_transactions as mt

HDR = ["name", "move_type", "state", "journal_id", "currency_id", "amount_untaxed", "amount_tax", "amount_total",
       "amount_total_signed"]
LNF = ["account_id", "display_type", "debit", "credit", "balance", "amount_currency", "price_unit", "quantity",
       "discount", "tax_ids", "currency_id", "tax_repartition_line_id"]


def show(label, conn, mid):
    hf = [f for f in HDR if f in (conn.fields("account.move") or {})]
    h = conn.call("account.move", "read", [mid], fields=hf)[0]
    print("\n%s header: %s" % (label, {k: v for k, v in h.items() if k != "id"}))
    lf = [f for f in LNF if f in (conn.fields("account.move.line") or {})]
    ids = conn.call("account.move.line", "search", [("move_id", "=", mid)], order="id")
    for r in conn.call("account.move.line", "read", ids, fields=lf):
        acc = r["account_id"][1][:28] if r.get("account_id") else ""
        print("  line %-6s %-28s type=%-13s dr=%-10s cr=%-10s bal=%-10s amt_cur=%-10s price=%-8s qty=%-5s tax=%s" % (
            r["id"], acc, r.get("display_type"), r.get("debit"), r.get("credit"), r.get("balance"),
            r.get("amount_currency"), r.get("price_unit"), r.get("quantity"), r.get("tax_ids")))


def main():
    sid = int(sys.argv[1])
    mt.src, mt.dst, mt.MAP = mt.Src(), mt.Dst(), mt.IdMap()
    did = mt.MAP.get("account.move", sid)
    print("src account.move#%s -> dst #%s" % (sid, did))
    show("SRC", mt.src, sid)
    if did:
        show("DST", mt.dst, did)


if __name__ == "__main__":
    main()
