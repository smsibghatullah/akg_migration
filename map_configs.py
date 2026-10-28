#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pos.config aur pos.payment.method ka many-to-one mapping (naam se) -> idmap.json

  python3 map_configs.py            # sirf report (kuch likhta nahi)
  python3 map_configs.py --apply    # idmap.json mein likho
"""
import sys
import collections
import migrate_transactions as mt

CFG, PM = "pos.config", "pos.payment.method"

# source naam -> destination naam (jo yahan nahi, wo apne hi naam se map hoga)
ALIAS = {
    "Maabela Shop": "Mabela Shop",
    "Ghubra Store": "Ghubra Shop",      # <- apna faisla yahan badlo
}
# in naamon ko migrate nahi karna (test data)
SKIP = {"Mabela Shop-Test", "Test", "Testing", "Ghubra Branch-test"}
# payment methods ke alias (zaroorat ho to)
PM_ALIAS = {}

CTX = {"active_test": False}


def build(model, skip, alias, apply_):
    src = mt.src.call(model, "search_read", [], fields=["name"], context=CTX)
    dst = mt.dst.call(model, "search_read", [], fields=["name"], context=CTX)
    dby = collections.defaultdict(list)
    for r in dst:
        dby[r["name"]].append(r["id"])
    dups = {n: ids for n, ids in dby.items() if len(ids) > 1}
    stats = collections.Counter()
    nodst, ambiguous = collections.Counter(), collections.Counter()
    for r in src:
        name = r["name"]
        if name in skip:
            stats["skipped(test)"] += 1
            continue
        if mt.MAP.get(model, r["id"]):
            stats["pehle se mapped"] += 1
            continue
        target = alias.get(name, name)
        ids = dby.get(target, [])
        if not ids:
            nodst[target] += 1
            continue
        if len(ids) > 1:
            ambiguous[target] += 1
            continue
        if apply_:
            mt.MAP.set_master(model, r["id"], ids[0])
        stats["naya map"] += 1
    print("\n== %s ==" % model)
    for k, v in stats.items():
        print("  %-18s %d" % (k, v))
    if nodst:
        print("  Odoo 20 mein NAHI (pehle banao):")
        for n, c in nodst.most_common():
            print("     %-30s %d source records" % (n, c))
    if ambiguous:
        print("  Odoo 20 mein DUPLICATE naam (ek rakho, baqi archive/delete karo):")
        for n, c in ambiguous.most_common():
            print("     %-30s dst ids %s" % (n, dups[n]))


def main():
    apply_ = "--apply" in sys.argv
    mt.src, mt.dst, mt.MAP = mt.Src(), mt.Dst(), mt.IdMap()
    build(CFG, SKIP, ALIAS, apply_)
    build(PM, set(), PM_ALIAS, apply_)
    if apply_:
        mt.MAP.save_master()
        print("\nidmap.json update ho gayi.")
    else:
        print("\n[REPORT ONLY] likhne ke liye --apply lagao.")


if __name__ == "__main__":
    main()
