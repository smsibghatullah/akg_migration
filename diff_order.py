#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ek migrate hue order ko source (Odoo 16) aur destination (Odoo 20) mein line-by-line dikhata hai.

  python3 diff_order.py 38673                 # sale.order (source id)
  python3 diff_order.py 38673 sale.order
  python3 diff_order.py 123 purchase.order

Kuch likhta/badalta nahi, sirf parhta hai.
"""
import sys

import migrate_transactions as mt

LM = {"sale.order": ("sale.order.line", "order_id"), "purchase.order": ("purchase.order.line", "order_id")}
HDR = ["name", "state", "pricelist_id", "currency_id", "fiscal_position_id", "payment_term_id",
       "amount_untaxed", "amount_tax", "amount_total"]
LINE = ["product_id", "product_uom_qty", "product_qty", "product_uom", "product_uom_id", "price_unit", "discount",
        "tax_id", "tax_ids", "taxes_id", "price_subtotal", "price_tax", "price_total", "display_type"]


def show(conn, label, model, rid):
    lm, fk = LM[model]
    f = conn.fields(model)
    print("\n" + "=" * 20, label, model, "#%s" % rid, "=" * 20)
    h = conn.call(model, "read", [rid], fields=[x for x in HDR if x in f], context=mt.CTX)[0]
    print({k: v for k, v in h.items() if k != "id"})
    lf = conn.fields(lm)
    lids = conn.call(lm, "search", [(fk, "=", rid)], order="id", context=mt.CTX)
    rows = conn.call(lm, "read", lids, fields=[x for x in LINE if x in lf], context=mt.CTX) if lids else []
    tax_ids = set()
    for r in rows:
        for k in ("tax_id", "tax_ids", "taxes_id"):
            tax_ids |= set(r.get(k) or [])
    names = {}
    if tax_ids:
        for t in conn.call("account.tax", "read", sorted(tax_ids),
                           fields=[x for x in ("name", "amount", "price_include") if x in conn.fields("account.tax")],
                           context=mt.CTX):
            names[t["id"]] = "%s (%s%%, incl=%s)" % (t.get("name"), t.get("amount"), t.get("price_include"))
    for i, r in enumerate(rows, 1):
        r = dict(r)
        for k in ("tax_id", "tax_ids", "taxes_id"):
            if k in r:
                r[k] = [names.get(x, x) for x in r[k]]
        r.pop("id", None)
        print(" line %d: %s" % (i, r))
    try:
        c = conn.call("res.company", "search_read", [], fields=[x for x in ("name", "tax_calculation_rounding_method")
                      if x in conn.fields("res.company")], context=mt.CTX)
        print(" company:", c)
    except Exception as e:
        print(" company read fail:", str(e)[:100])
    try:
        dp = conn.call("decimal.precision", "search_read", [("name", "in", ["Product Price", "Discount", "Product Unit"])],
                       fields=["name", "digits"], context=mt.CTX)
        print(" decimal precision:", {d["name"]: d["digits"] for d in dp})
    except Exception as e:
        print(" decimal precision read fail:", str(e)[:100])


def main():
    if len(sys.argv) < 2:
        sys.exit("Use: python3 diff_order.py <source_id> [sale.order|purchase.order]")
    sid = int(sys.argv[1])
    model = sys.argv[2] if len(sys.argv) > 2 else "sale.order"
    mt.src, mt.dst = mt.Src(), mt.Dst()
    mt.MAP = mt.IdMap()
    did = mt.MAP.get(model, sid)
    show(mt.src, "SOURCE (Odoo 16)", model, sid)
    if did:
        show(mt.dst, "DESTINATION (Odoo 20)", model, did)
    else:
        print("\nYe order destination mein migrate nahi hua (map mein nahi).")


if __name__ == "__main__":
    main()
