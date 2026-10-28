#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Destination mein product ki quantity (quants) maujood hai magar qty_available zero? Wajah dhoondne ke liye (read-only).

  python3 diagnose_locations.py 431        # 431 = Odoo 20 ka product.product id
"""
import sys
import migrate_transactions as mt

CTX = {"active_test": False}


def pick(model, wanted):
    f = mt.dst.fields(model) or {}
    return [x for x in wanted if x in f]


def main():
    mt.dst = mt.Dst()
    pid = int(sys.argv[1])
    comps = mt.dst.call("res.company", "search_read", [], fields=["name"], context=CTX)
    cids = [c["id"] for c in comps]
    print("Companies:", {c["id"]: c["name"] for c in comps})
    ctx_user = mt.dst.call("res.users", "context_get")
    print("API user ki context: allowed_company_ids=%s tz=%s" % (ctx_user.get("allowed_company_ids"), ctx_user.get("tz")))

    whs = mt.dst.call("stock.warehouse", "search_read", [], fields=pick(
        "stock.warehouse", ["name", "code", "company_id", "view_location_id", "lot_stock_id", "active"]), context=CTX)
    print("\nWarehouses (%d):" % len(whs))
    for w in whs:
        print("  #%s %s [%s] company=%s view=%s stock=%s active=%s" % (
            w["id"], w["name"], w.get("code"), w.get("company_id"), w.get("view_location_id"),
            w.get("lot_stock_id"), w.get("active")))

    qs = mt.dst.call("stock.quant", "search_read", [("product_id", "=", pid), ("location_id.usage", "=", "internal")],
                     fields=pick("stock.quant", ["location_id", "quantity", "company_id"]), context=CTX)
    locs = sorted({mt.m2o_id(q["location_id"]) for q in qs})
    lrows = {r["id"]: r for r in mt.dst.call("stock.location", "read", locs, fields=pick(
        "stock.location", ["complete_name", "company_id", "warehouse_id", "parent_path", "active"]), context=CTX)}
    print("\nProduct #%s ke internal quants (%d):" % (pid, len(qs)))
    for q in qs:
        l = lrows[mt.m2o_id(q["location_id"])]
        print("  qty %-8s %-34s quant_company=%s loc_company=%s warehouse=%s active=%s" % (
            q["quantity"], l.get("complete_name"), q.get("company_id"), l.get("company_id"),
            l.get("warehouse_id"), l.get("active")))
        if not l.get("warehouse_id"):
            print("      ^ is location ka koi warehouse nahi: qty_available mein ye shamil nahi hogi")

    print("\nqty_available alag alag context mein:")
    for label, ctx in (("default (API user)", {}), ("saari companies", {"allowed_company_ids": cids})):
        r = mt.dst.call("product.product", "read", [pid], fields=["qty_available"], context=dict(ctx, **CTX))[0]
        print("  %-20s %s" % (label, r["qty_available"]))
    for w in whs:
        r = mt.dst.call("product.product", "read", [pid], fields=["qty_available"],
                        context=dict(CTX, warehouse_id=w["id"], allowed_company_ids=cids))[0]
        print("  warehouse %-10s %s" % (w.get("code"), r["qty_available"]))


if __name__ == "__main__":
    main()
