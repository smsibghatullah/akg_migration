#!/usr/bin/env python3
"""
POS configs jo migrate nahi hue (jaise 652 'Distribution Sales (not used)') unhe migrate karo.

Wajah: Odoo 20 mein aik CASH payment method sirf aik hi POS mein use ho sakta hai.
Source POS ka cash method pehle se kisi aur POS ke saath linked ho to create fail hota tha:
   "This cash payment method is already used in another Point of Sale."

Ye script wahi cash method us POS se hata deti hai (baqi sab payment methods rehte hain). Agar koi method
na bache to Odoo ke apne default payment methods lag jate hain. Hata hua method log mein warning ke saath aata hai.

  python3 migrate_pos_configs.py --dry-run          # sirf dikhao kaun se POS migrate honge
  python3 migrate_pos_configs.py                    # jo ACTIVE POS abhi migrate nahi hue wo migrate karo
  python3 migrate_pos_configs.py --ids 652          # sirf is source POS id ko
  python3 migrate_pos_configs.py --include-archived # ARCHIVED (band) POS configs bhi - purani sessions/orders ke liye zaroori

odoo16_to_20_migrate.py wale folder mein rakho (wahi idmap.json use hota hai).
"""
import argparse

import odoo16_to_20_migrate as m

log = m.log
CTX = m.CTX


def drop_taken_cash_methods(vals, rec):
    """Jo cash payment methods dst mein kisi aur POS par lage hain unhe is POS ki list se hata do"""
    pm = vals.get("payment_method_ids")
    if not pm or m.DRY:
        return
    ids = list(pm[0][2])
    df = m.dst.fields("pos.payment.method") or {}
    cashf = "type" if "type" in df else ("is_cash_count" if "is_cash_count" in df else None)
    if not cashf or "config_ids" not in df or not ids:
        return
    mine = m.idmap.get("pos.config", rec["id"])
    rows = m.dst.call("pos.payment.method", "search_read", [("id", "in", ids)],
                      fields=["name", cashf, "config_ids"], context=CTX)
    taken, seen_cash, keep = [], False, []
    for r in rows:
        is_cash = (r[cashf] == "cash") if cashf == "type" else bool(r[cashf])
        used_by = [c for c in (r.get("config_ids") or []) if c != mine]
        if is_cash and (used_by or seen_cash):          # kisi aur POS ka, ya is POS ka doosra cash method
            taken.append((r["id"], r["name"], used_by))
        else:
            keep.append(r["id"])
            seen_cash = seen_cash or is_cash
    if not taken:
        return
    gone = {t[0] for t in taken}
    keep = [i for i in ids if i not in gone]
    for pid, name, used_by in taken:
        log.warning("pos.config '%s': cash payment method '%s' (dst id %s) hata diya (POS %s mein pehle se / ek POS mein "
                    "sirf ek cash method)", rec.get("name"), name, pid, used_by)
    if keep:
        vals["payment_method_ids"] = [[6, 0, keep]]
    else:
        vals.pop("payment_method_ids", None)          # Odoo ke default payment methods lagenge


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", help="comma separated source pos.config ids (default: abhi tak migrate na hue active POS)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--include-archived", action="store_true",
                    help="archived POS configs bhi (unki purani sessions migrate karne ke liye)")
    a = ap.parse_args()

    m.src, m.dst = m.Odoo(m.SRC), m.Odoo2(m.DST)
    m.idmap = m.IdMap(m.IDMAP_FILE)
    m.DRY = a.dry_run
    m.seed_companies()

    step = next(s for s in m.STEPS if s["model"] == "pos.config")
    orig = step["hook"]

    def hook(vals, rec, later):
        vals = orig(vals, rec, later)
        if vals is not None:
            drop_taken_cash_methods(vals, rec)
        return vals

    st = dict(step)
    st["hook"] = hook

    sm = m.src_model("pos.config")
    if a.ids:
        ids = [int(x) for x in a.ids.split(",")]
    else:
        allids = m.src.call(sm, "search", [] if a.include_archived else [("active", "=", True)], context=CTX)
        ids = [i for i in allids if not m.idmap.get("pos.config", i)]
    if not ids:
        print("Koi POS config baqi nahi - sab migrate ho chuke.")
        return
    names = {r["id"]: r.get("name") for r in m.src.call(sm, "read", ids, fields=["name"], context=CTX)}
    print("Ye POS configs migrate honge:")
    for i in ids:
        print("   src %s  %s" % (i, names.get(i)))
    m.DOMAIN_OVERRIDE = [("id", "in", ids)]
    m.run_step(st)
    m.flush_pending(final=True)
    m.idmap.save()
    left = [i for i in ids if not m.idmap.get("pos.config", i)] if not a.dry_run else []
    if left:
        print("\nABHI BHI migrate nahi hue: %s  -> migration.log mein ' ERROR ' wali aakhri lines dekho." % left)
    else:
        print("\nDone.")


if __name__ == "__main__":
    main()
