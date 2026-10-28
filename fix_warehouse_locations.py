#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Migrate hui stock locations ko warehouse ke apne view location ke neeche le jata hai,
taake product ki qty_available mein ginti ho.

  python3 fix_warehouse_locations.py                       # DRY-RUN: sirf dikhata hai kya hoga
  python3 fix_warehouse_locations.py --apply               # asal mein karta hai
  python3 fix_warehouse_locations.py --apply --check 431   # baad mein is dst product ki qty_available dikhao

Har source warehouse (code se mel kha kar) ke liye:
  migrated view location (V) ke saare ACTIVE direct children -> destination warehouse ke view location (WV) ke neeche.
Sirf parent badalta hai: kuch delete/archive nahi hota. Wapas karne ke liye har location ka purana parent output mein likha hota hai.

Pehle database ka backup lo.
"""
import sys
import migrate_transactions as mt

CTX = {"active_test": False}


def main():
    apply = "--apply" in sys.argv
    check = int(sys.argv[sys.argv.index("--check") + 1]) if "--check" in sys.argv else None
    mt.src, mt.dst, mt.MAP = mt.Src(), mt.Dst(), mt.IdMap()
    swhs = mt.src.call("stock.warehouse", "search_read", [], fields=["code", "name", "view_location_id", "active"],
                       context=CTX)
    dwhs = {w["code"]: w for w in mt.dst.call("stock.warehouse", "search_read", [],
                                              fields=["code", "name", "view_location_id", "active"], context=CTX)}
    moved = 0
    print("%s\n" % ("APPLY" if apply else "DRY-RUN (kuch nahi badla ja raha; --apply se asal mein hoga)"))
    for sw in swhs:
        code = sw["code"]
        dw = dwhs.get(code)
        if not dw:
            print("[%s] destination mein is code ka warehouse nahi - skip" % code)
            continue
        if not dw["active"]:
            print("[%s] destination warehouse archived hai - skip" % code)
            continue
        v = mt.resolve("stock.location", mt.m2o_id(sw["view_location_id"]))
        wv = mt.m2o_id(dw["view_location_id"])
        if not v:
            print("[%s] source view location ka map nahi - skip" % code)
            continue
        if v == wv:
            print("[%s] pehle se theek (view location ek hi hai)" % code)
            continue
        kids = mt.dst.call("stock.location", "search_read", [("location_id", "=", v), ("active", "=", True)],
                           fields=["complete_name", "usage"], context=CTX)
        print("[%s] migrated view #%s -> warehouse view #%s | shift hone wale active children: %d" % (
            code, v, wv, len(kids)))
        for k in kids:
            print("      #%s %s (%s)  [purana parent: #%s]" % (k["id"], k["complete_name"], k["usage"], v))
        if apply and kids:
            mt.dst.call("stock.location", "write", [k["id"] for k in kids], {"location_id": wv}, context=mt.CTX)
            moved += len(kids)
    print("\n%s: %d locations" % ("shift hui" if apply else "shift ho sakti hain", moved if apply else sum(1 for _ in [])))
    if apply and check:
        cids = [c["id"] for c in mt.dst.call("res.company", "search_read", [], fields=["name"], context=CTX)]
        r = mt.dst.call("product.product", "read", [check], fields=["qty_available", "virtual_available"],
                        context=dict(CTX, allowed_company_ids=cids))[0]
        print("product #%s: qty_available=%s virtual_available=%s" % (check, r["qty_available"], r["virtual_available"]))
    if not apply:
        print("Theek lage to: python3 fix_warehouse_locations.py --apply --check 431")


if __name__ == "__main__":
    main()
