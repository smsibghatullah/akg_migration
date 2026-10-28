#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Destination ki stock locations ka tree (read-only). Kaun si migrate hui (M) aur kaun si warehouse ne khud banayi.

  python3 diagnose_location_tree.py            # saari view/internal locations
  python3 diagnose_location_tree.py > tree.txt # file mein save
"""
import migrate_transactions as mt

CTX = {"active_test": False}


def main():
    mt.dst, mt.MAP = mt.Dst(), mt.IdMap()
    migrated = mt.MAP.dst_ids("stock.location")
    whs = mt.dst.call("warehouse_placeholder" if False else "stock.warehouse", "search_read", [],
                      fields=["code", "view_location_id", "lot_stock_id", "active"], context=CTX)
    wh_view = {mt.m2o_id(w["view_location_id"]): w["code"] for w in whs}
    wh_stock = {mt.m2o_id(w["lot_stock_id"]): w["code"] for w in whs}
    fl = [f for f in ("complete_name", "usage", "location_id", "warehouse_id", "active", "parent_path")
          if f in (mt.dst.fields("stock.location") or {})]
    locs = mt.dst.call("stock.location", "search_read", [("usage", "in", ["view", "internal"])], fields=fl,
                       order="complete_name, id", context=CTX)
    print("legend: M = migrate hui location | WV = warehouse ki view location | WS = warehouse ka lot_stock")
    print("%-6s %-3s %-4s %-9s %-7s %-6s %s" % ("id", "M", "tag", "usage", "parent", "quants", "complete_name (warehouse_id)"))
    for l in locs:
        pid = mt.m2o_id(l.get("location_id")) or "-"
        tag = "WV" if l["id"] in wh_view else ("WS" if l["id"] in wh_stock else "")
        q = ""
        if l["usage"] == "internal":
            q = mt.dst.call("stock.quant", "search_count", [("location_id", "=", l["id"])], context=CTX)
        print("%-6s %-3s %-4s %-9s %-7s %-6s %s%s" % (
            l["id"], "M" if l["id"] in migrated else "", tag, l["usage"], pid, q, l.get("complete_name"),
            "" if l.get("active", True) else "  [archived]"))
        if l.get("warehouse_id"):
            print("%38s -> warehouse %s" % ("", l["warehouse_id"][1]))


if __name__ == "__main__":
    main()
