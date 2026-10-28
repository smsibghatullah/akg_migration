#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ek product ki on-hand quantity aur value, Odoo 16 vs Odoo 20 (read-only).

  python3 diagnose_stock.py 123          # 123 = Odoo 16 ka product.product id
  python3 diagnose_stock.py 123 456      # kai products
"""
import sys
import migrate_transactions as mt

CTX = {"active_test": False}
PF = ["display_name", "default_code", "standard_price", "avg_cost", "total_value", "qty_available", "virtual_available",
      "is_storable", "type", "tracking", "categ_id"]
CF = ["property_cost_method", "property_valuation", "property_stock_valuation_account_id"]
MF = ["reference", "state", "date", "product_uom_qty", "quantity", "value", "price_unit", "location_id",
      "location_dest_id", "is_inventory"]


def pick(conn, model, wanted):
    f = conn.fields(model) or {}
    return [x for x in wanted if x in f]


def show_product(label, conn, pid):
    r = conn.call("product.product", "read", [pid], fields=pick(conn, "product.product", PF), context=CTX)[0]
    print("\n%s product #%s" % (label, pid))
    for k, v in r.items():
        if k != "id":
            print("    %-18s %s" % (k, v))
    if r.get("categ_id"):
        c = conn.call("product.category", "read", [mt.m2o_id(r["categ_id"])],
                      fields=pick(conn, "product.category", CF), context=CTX)[0]
        print("    category: %s" % {k: v for k, v in c.items() if k != "id"})
    q = conn.call("stock.quant", "search_read", [("product_id", "=", pid), ("location_id.usage", "=", "internal")],
                  fields=pick(conn, "stock.quant", ["location_id", "lot_id", "quantity", "value", "inventory_quantity"]),
                  context=CTX)
    print("    internal quants: %d | total qty %s" % (len(q), sum(x.get("quantity") or 0 for x in q)))
    for x in q[:8]:
        print("      %s" % {k: v for k, v in x.items() if k != "id"})
    return r


def find_product(conn, pid):
    if conn.call("product.product", "search", [("id", "=", pid)], context=CTX):
        return pid
    ids = conn.call("product.product", "search", [("product_tmpl_id", "=", pid)], context=CTX)
    return ids[0] if ids else None


def main():
    mt.src, mt.dst, mt.MAP = mt.Src(), mt.Dst(), mt.IdMap()
    for sid in [int(x) for x in sys.argv[1:]]:
        real = find_product(mt.src, sid)
        if not real:
            print("\nSource mein #%s na product.product mila na product.template." % sid)
            continue
        if real != sid:
            print("\n#%s product.product nahi, product.template tha -> product.product #%s le raha hun" % (sid, real))
            sid = real
        s = show_product("SRC", mt.src, sid)
        did = mt.resolve("product.product", sid)
        if not did:
            print("\nDST: is product ka map nahi hai")
            continue
        d = show_product("DST", mt.dst, did)
        mv = mt.dst.call("stock.move", "search_read", [("product_id", "=", did)],
                         fields=pick(mt.dst, "stock.move", MF), order="id desc", limit=6, context=CTX)
        print("\n    DST stock.move (aakhri %d):" % len(mv))
        for m in mv:
            print("      %s" % {k: v for k, v in m.items() if k != "id"})
        sc, dc = s.get("standard_price"), d.get("standard_price")
        if sc and not dc:
            print("\n>>> Source mein cost %s hai magar destination mein 0: isi wajah se value zero hai." % sc)


if __name__ == "__main__":
    main()
