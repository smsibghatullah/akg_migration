#!/usr/bin/env python3
"""
Aik migrate hui entry/invoice ko Odoo 16 aur Odoo 20 mein side-by-side dikhao (read-only).

  python3 diag_move.py 33            # source move id 33 (dst id txmap.sqlite3 se khud dhoondta hai)
  python3 diag_move.py 33 --dst 31   # dst id khud do

Output mein: header (type, state, amounts) aur har line ke debit/credit/balance/amount_currency/display_type/tax fields.
Ye output yahan bhej do, usi se pata chalega ke Odoo 20 total 0 kyun nikal raha hai.
"""
import argparse
import os
import sqlite3

import odoo16_to_20_migrate as m

CTX = m.CTX
HDR = ["name", "move_type", "state", "journal_id", "partner_id", "currency_id", "date", "invoice_date", "amount_untaxed",
       "amount_tax", "amount_total", "amount_residual", "payment_state", "is_storno"]
LINE = ["id", "account_id", "display_type", "name", "debit", "credit", "balance", "amount_currency", "currency_id",
        "currency_rate", "price_unit", "quantity", "price_subtotal", "price_total", "tax_ids", "tax_line_id",
        "tax_repartition_line_id", "product_id", "partner_id", "parent_state"]


def short(v):
    if isinstance(v, (list, tuple)) and len(v) == 2 and isinstance(v[0], int):
        return "%s:%s" % (v[0], str(v[1])[:22])
    return str(v)[:26]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src_id", type=int)
    ap.add_argument("--dst", type=int)
    a = ap.parse_args()
    m.src, m.dst = m.Odoo(m.SRC), m.Odoo2(m.DST)
    did = a.dst
    if not did and os.path.exists("txmap.sqlite3"):
        r = sqlite3.connect("txmap.sqlite3").execute(
            "select dst from m where model='account.move' and src=?", (a.src_id,)).fetchone()
        did = r[0] if r else None
    if not did:
        raise SystemExit("dst id nahi mili - --dst do")
    sides = {}
    for side, conn, mid in (("ODOO16", m.src, a.src_id), ("ODOO20", m.dst, did)):
        hf = [f for f in HDR if f in (conn.fields("account.move") or {})]
        lf = [f for f in LINE if f in (conn.fields("account.move.line") or {})]
        h = conn.call("account.move", "read", [mid], fields=hf, context=CTX)[0]
        lids = conn.call("account.move.line", "search", [("move_id", "=", mid)], order="id", context=CTX)
        ls = conn.call("account.move.line", "read", lids, fields=lf, context=CTX) if lids else []
        sides[side] = (h, ls, lf)
        print("\n=============== %s  account.move#%s ===============" % (side, mid))
        for f in hf:
            print("   %-16s %s" % (f, short(h.get(f))))
        print("   --- %d lines ---" % len(ls))
        for l in ls:
            print("   " + " | ".join("%s=%s" % (f, short(l.get(f))) for f in lf if l.get(f) not in (False, None, 0, 0.0, [])))
        print("   SUM debit %.2f credit %.2f" % (sum(l.get("debit") or 0 for l in ls), sum(l.get("credit") or 0 for l in ls)))
    s, d = sides["ODOO16"][2], sides["ODOO20"][2]
    print("\nLINE FIELDS sirf Odoo16 mein: %s" % sorted(set(s) - set(d)))
    print("LINE FIELDS sirf Odoo20 mein: %s" % sorted(set(d) - set(s)))
    f20 = m.dst.fields("account.move.line") or {}
    for f in ("debit", "credit", "balance", "amount_currency", "display_type"):
        if f in f20:
            print("Odoo20 account.move.line.%-16s type=%s readonly=%s store=%s" % (
                f, f20[f]["type"], f20[f].get("readonly"), f20[f].get("store")))


if __name__ == "__main__":
    main()
