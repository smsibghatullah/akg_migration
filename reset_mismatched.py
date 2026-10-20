#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Jo migrate hue documents ka amount_total source se match nahi karta, unhe Odoo 20 se hata kar map se nikalta hai,
taake fixed script se dobara migrate ho sakein. Jin ka amount match karta hai wo chhede nahi jate.

  python3 reset_mismatched.py sale.order            # sirf list dikhata hai (kuch delete nahi)
  python3 reset_mismatched.py sale.order --apply    # delete + map se nikalo
  python3 reset_mismatched.py purchase.order --apply

Pehle migrate_transactions.py ke saath wali folder mein chalao. idmap.json aur txmap.sqlite3 ka backup le lo.
"""
import sys

import migrate_transactions as mt

LINES = {"sale.order": ("sale.order.line", "order_id"), "purchase.order": ("purchase.order.line", "order_id")}


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in LINES:
        sys.exit("Use: python3 reset_mismatched.py sale.order|purchase.order [--apply]")
    model, apply = sys.argv[1], "--apply" in sys.argv
    lm, fk = LINES[model]
    mt.src, mt.dst = mt.Src(), mt.Dst()
    mt.MAP = mt.IdMap()
    pairs = [(s, mt.MAP.get(model, s)) for s in sorted(mt.MAP.done_src_ids(model))]
    pairs = [(s, d) for s, d in pairs if d]
    if not pairs:
        print("Koi migrate hua %s nahi mila." % model)
        return
    _, sper = mt._sum_read(mt.src, model, [p[0] for p in pairs], ["amount_total"])
    _, dper = mt._sum_read(mt.dst, model, [p[1] for p in pairs], ["amount_total"])
    bad = [(s, d) for s, d in pairs
           if d not in dper or abs((sper[s]["amount_total"] or 0) - (dper[d]["amount_total"] or 0)) > 0.011]
    print("%s: migrate hue %d | amount match %d | galat %d" % (model, len(pairs), len(pairs) - len(bad), len(bad)))
    for s, d in bad[:10]:
        print("   src#%s dst#%s  %s vs %s" % (s, d, sper[s]["amount_total"], dper.get(d, {}).get("amount_total")))
    if not apply:
        print("\nDry-run. Delete ke liye --apply lagao.")
        return
    dfields = mt.dst.fields(model)
    done = 0
    for s, d in bad:
        try:
            dl = mt.dst.call(lm, "search", [(fk, "=", d)], context=mt.CTX)
            vals = {"state": "draft"}
            if "locked" in dfields:
                vals["locked"] = False
            mt.dst.call(model, "write", [d], vals, context=mt.CTX)
            mt.dst.call(model, "unlink", [d], context=mt.CTX)
            mt.MAP.drop(model, s)
            for l in dl:
                mt.MAP.db.execute("delete from m where model=? and dst=?", (lm, l))
            done += 1
        except Exception as e:
            print("   FAIL src#%s dst#%s: %s" % (s, d, str(e)[:200]))
    mt.MAP.commit()
    mt.MAP.save_master()
    print("%d documents hata diye. Ab dobara `run --steps %s` chalao." % (done, "sales" if model == "sale.order" else "purchases"))


if __name__ == "__main__":
    main()
