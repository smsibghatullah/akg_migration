#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ek migrate hue document ki lines Odoo 16 vs Odoo 20 side by side (total kyun nahi mil raha).

  python3 diagnose_order.py sale.order 38501
  python3 diagnose_order.py sale.order 38508
"""
import sys
import migrate_transactions as mt

CFG = {
    "sale.order": ("sale.order.line", "order_id", "tax_id"),
    "purchase.order": ("purchase.order.line", "order_id", "taxes_id"),
}


def taxes(conn, ids):
    if not ids:
        return []
    return [(t["id"], t["name"], t["amount_type"], t["amount"], t.get("price_include"))
            for t in conn.call("account.tax", "read", ids, fields=["name", "amount_type", "amount", "price_include"],
                               context={"active_test": False})]


def main():
    model, sid = sys.argv[1], int(sys.argv[2])
    lm, fk, tf = CFG[model]
    mt.src, mt.dst, mt.MAP = mt.Src(), mt.Dst(), mt.IdMap()
    did = mt.MAP.get(model, sid)
    print("src %s#%s -> dst #%s" % (model, sid, did))
    hf = ["partner_id", "pricelist_id", "fiscal_position_id", "currency_id", "amount_untaxed", "amount_tax", "amount_total"]
    for label, conn, i in (("SRC", mt.src, sid), ("DST", mt.dst, did)):
        h = conn.call(model, "read", [i], fields=[f for f in hf if f in (conn.fields(model) or {})])[0]
        print("\n%s header: %s" % (label, {k: v for k, v in h.items() if k != "id"}))
        lf = ["product_id", "product_uom_qty", "product_qty", "price_unit", "discount", tf, "price_subtotal",
              "price_tax", "price_total"]
        lids = conn.call(lm, "search", [(fk, "=", i)], order="id")
        rows = conn.call(lm, "read", lids, fields=[f for f in lf if f in (conn.fields(lm) or {})])
        for r in rows:
            print("  line %s | %s | qty %s | price %s | disc %s | sub %s | tax_amt %s | total %s" % (
                r["id"], (r.get("product_id") or ["", ""])[1][:30], r.get("product_uom_qty", r.get("product_qty")),
                r.get("price_unit"), r.get("discount", ""), r.get("price_subtotal"), r.get("price_tax"), r.get("price_total")))
            for t in taxes(conn, r.get(tf, [])):
                print("      tax %s %s (%s %s) price_include=%s" % t)


if __name__ == "__main__":
    main()
