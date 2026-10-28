#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
hr.salary.rule id-map: ("hr.salary.rule", src_id) -> dst_id
Regular Pay (src struct 1 -> dst 3) aur Bonus (src struct 8 -> dst 8) ke rules.
  python3 migrate_salary_rules.py          # dry-run (sirf check + dikhata hai)
  python3 migrate_salary_rules.py --apply  # map likhta hai
Shart: koi aur migrate_* process na chal raha ho (txmap.sqlite3 lock).
"""
import argparse
import sys

import migrate_transactions as mt

MODEL = "hr.salary.rule"
PAIRS = {
    # Regular Pay
    1: 24, 2: 25, 3: 29, 5: 27, 8: 31, 55: 74,
    # Bonus
    95: 75, 97: 64, 98: 65, 99: 69, 103: 70, 104: 71,
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    mt.src, mt.dst, mt.MAP = mt.Src(), mt.Dst(), mt.IdMap()
    ctx = {"active_test": False}

    sr = {r["id"]: r for r in mt.src.call(MODEL, "read", list(PAIRS), fields=["code", "name"], context=ctx)}
    dr = {r["id"]: r for r in mt.dst.call(MODEL, "read", list(PAIRS.values()),
                                          fields=["code", "name", "struct_ids"], context=ctx)}
    bad = 0
    for sid, did in PAIRS.items():
        s, d = sr.get(sid), dr.get(did)
        if not s or not d:
            print("  MISSING src %s ya dst %s" % (sid, did)); bad += 1; continue
        ok = s["code"] == d["code"]
        bad += (not ok)
        print("  %-3s %-14s -> %-3s %-14s struct=%s %s" % (
            sid, s["code"], did, d["code"], d["struct_ids"], "OK" if ok else "CODE MISMATCH"))

    lines = mt.src.call("hr.payslip.line", "search_read", [], fields=["salary_rule_id"], context=ctx)
    used = {l["salary_rule_id"][0] for l in lines if l["salary_rule_id"]}
    uncovered = sorted(used - set(PAIRS))
    print("Source payslip lines mein use hue rules: %d | map mein nahi: %s" % (len(used), uncovered or "koi nahi"))

    if bad:
        sys.exit("Mismatch hai, map nahi likha.")
    if not a.apply:
        print("\nYeh sirf dry-run tha. --apply lagayein.")
        return
    n = 0
    for sid, did in PAIRS.items():
        if mt.MAP.get(MODEL, sid):
            continue
        mt.MAP.set(MODEL, sid, did)
        n += 1
    mt.MAP.db.commit()
    print("Map likha: %d nayi entries" % n)


if __name__ == "__main__":
    main()
