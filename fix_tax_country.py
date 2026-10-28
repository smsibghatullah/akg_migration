#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Error: "This entry contains one or more taxes that are incompatible with your fiscal country"

Odoo 20 mein har tax ka country_id company ke fiscal country (account_fiscal_country_id) ke barabar hona chahiye.
Ye script destination (Odoo 20) ke taxes ko check karti hai.

  python3 fix_tax_country.py           # sirf report (kuch badalta nahi)
  python3 fix_tax_country.py --fix     # galat country wale taxes ko company ke fiscal country par set karta hai
"""
import sys
import migrate_transactions as mt


def main():
    fix = "--fix" in sys.argv
    mt.dst = mt.Dst()
    ctx = {"active_test": False}
    comps = mt.dst.call("res.company", "search_read", [], fields=["name", "account_fiscal_country_id", "country_id"],
                        context=ctx)
    fiscal = {}
    print("Companies:")
    for c in comps:
        fc = mt.m2o_id(c.get("account_fiscal_country_id")) or None
        fiscal[c["id"]] = fc
        print("  #%s %s | fiscal country: %s | company country: %s" % (
            c["id"], c["name"], c.get("account_fiscal_country_id") or "KHALI", c.get("country_id") or "-"))
        if not fc:
            print("    -> is company ka fiscal country khali hai. Accounting > Settings > Fiscal Country set karo, phir ye script dobara chalao.")
    df = mt.dst.fields("account.tax") or {}
    if "country_id" not in df:
        print("account.tax par country_id field nahi mili - Odoo version alag hai, mujhe fields_get dikhao.")
        return
    taxes = mt.dst.call("account.tax", "search_read", [], fields=["name", "type_tax_use", "country_id", "company_id", "amount"],
                        context=ctx)
    bad = []
    for t in taxes:
        fc = fiscal.get(mt.m2o_id(t.get("company_id")))
        tc = mt.m2o_id(t.get("country_id")) or None
        if fc and tc != fc:
            bad.append((t, fc))
    print("\nTotal taxes: %d | country mismatch: %d" % (len(taxes), len(bad)))
    for t, fc in bad:
        print("  tax #%s %-30s %-9s %5s%%  country=%s  (chahiye: %s)" % (
            t["id"], t["name"][:30], t["type_tax_use"], t.get("amount"), t.get("country_id") or "KHALI", fc))
    if not bad:
        print("Taxes theek hain. Phir error ki wajah kuch aur hai - ek failed move ka diagnose_move.py output bhejo.")
        return
    if not fix:
        print("\nTheek karne ke liye: python3 fix_tax_country.py --fix")
        return
    for t, fc in bad:
        try:
            mt.dst.call("account.tax", "write", [t["id"]], {"country_id": fc}, context=mt.CTX)
            print("  fixed tax #%s" % t["id"])
        except Exception as e:
            print("  FAIL tax #%s: %s" % (t["id"], str(e)[:200]))
    print("\nAb moves dobara chalao, jo fail hue the wo khud dobara try honge (unka map nahi bana tha).")


if __name__ == "__main__":
    main()
