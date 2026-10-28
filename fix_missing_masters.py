#!/usr/bin/env python3
"""
Transaction migration jin masters ki wajah se ruki (missing_masters.csv) unhe master engine se migrate karo.

Kab: `migrate_transactions*.py run` ke baad "Skip ...: master map nahi: product_id: product.product#N" jaisi warnings
     aayen aur missing_masters.csv (relation, src_id, documents_ruke) ban jaye.

  python3 fix_missing_masters.py                      # missing_masters.csv padh kar sab fix
  python3 fix_missing_masters.py --file other.csv
  python3 fix_missing_masters.py --dry-run            # sirf dikhao kya karega

Kya karta hai:
  res.partner        sirf wahi partners migrate (master step, domain id in [...])
  product.product   un variants ke templates + attribute lines/values + saare siblings variants migrate
                     (jaise 'Transport to ...' jin mein City attribute ke variants hain)
  baqi models        agar master STEPS mein step ho to wahi, sirf in ids ke liye
Phir har id ka natija batata hai, aur jo na bane unki wajah migration.log se dikhata hai.
Uske baad: python3 migrate_transactions_ext.py run --steps sales   (jo skip hue the ab ban jayenge)

idmap.json update hoti hai (transaction script usay agli run par parh leti hai). Pehle backup khud ban jata hai.
"""
import argparse
import collections
import csv
import shutil
import time

import odoo16_to_20_migrate as m

log = m.log
CTX = m.CTX


def load_missing(path):
    out = collections.defaultdict(set)
    with open(path, encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            out[r["relation"]].add(int(r["src_id"]))
    return out


def run_ids(model, ids, field="id"):
    """Master engine ka step, sirf in ids ke liye"""
    steps = [s for s in m.STEPS if s["model"] == model]
    if not steps:
        print("   %s ka master step nahi - manually migrate karo" % model)
        return False
    m.DOMAIN_OVERRIDE = [(field, "in", sorted(ids))]
    try:
        for st in steps:
            m.run_step(st)
    finally:
        m.DOMAIN_OVERRIDE = None
    m.flush_pending()
    m.idmap.save()
    return True


def fix_products(ids):
    sm = "product.product"
    vs = m.src.call(sm, "read", sorted(ids), fields=["product_tmpl_id", "display_name"], context=CTX)
    tids = sorted({m.m2o_id(v["product_tmpl_id"]) for v in vs})
    print("   %d variants, %d templates" % (len(vs), len(tids)))
    # attribute masters jo in templates ko chahiye
    al = m.src.call("product.template.attribute.line", "search_read", [("product_tmpl_id", "in", tids)],
                    fields=["attribute_id", "value_ids"], context=CTX)
    attrs = sorted({m.m2o_id(a["attribute_id"]) for a in al})
    vals = sorted({v for a in al for v in a["value_ids"]})
    if attrs:
        run_ids("product.attribute", attrs)
        run_ids("product.attribute.value", vals)
    run_ids("product.template", tids)
    run_ids("product.template.attribute.line", tids, field="product_tmpl_id")
    sib = m.src.call(sm, "search", [("product_tmpl_id", "in", tids)], context=CTX)
    run_ids(sm, sib)           # siblings bhi, taake pura template theek ho


def reasons(model, sid):
    """Is id ke baare mein migration.log ki aakhri lines"""
    keys = ("src id %s " % sid, "variant %s" % sid, "#%s" % sid, "%s " % sid)
    hits = []
    try:
        with open("migration.log", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if (" ERROR " in line or " WARNING " in line) and any(k in line for k in keys[:2]):
                    hits.append(line.strip()[:260])
    except FileNotFoundError:
        pass
    return hits[-2:]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="missing_masters.csv")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    miss = load_missing(a.file)
    if not miss:
        raise SystemExit("%s khali hai - kuch fix karne ko nahi." % a.file)
    print("missing masters (%s):" % a.file)
    for rel, ids in miss.items():
        print("   %-24s %3d ids  %s%s" % (rel, len(ids), sorted(ids)[:8], " ..." if len(ids) > 8 else ""))
    if a.dry_run:
        return
    m.src, m.dst = m.Odoo(m.SRC), m.Odoo2(m.DST)
    m.idmap = m.IdMap(m.IDMAP_FILE)
    m.seed_companies()
    shutil.copy(m.IDMAP_FILE, "%s.bak_fixmasters_%s" % (m.IDMAP_FILE, time.strftime("%Y%m%d_%H%M%S")))
    for rel, ids in miss.items():
        todo = sorted(i for i in ids if not m.idmap.get(m.src_model(rel) or rel, i))
        print("\n== %s: %d ids (pehle se mapped: %d)" % (rel, len(todo), len(ids) - len(todo)))
        if not todo:
            continue
        sm = m.src_model(rel)
        if not sm:
            print("   Odoo16 mein model nahi")
            continue
        if rel == "product.product":
            fix_products(set(todo))
        else:
            run_ids(rel, todo)
    print("\n================ NATIJA ================")
    bad = 0
    for rel, ids in miss.items():
        sm = m.src_model(rel) or rel
        names = {}
        try:
            for r in m.src.call(sm, "read", sorted(ids), fields=["display_name"], context=CTX):
                names[r["id"]] = r["display_name"]
        except Exception:
            pass
        ok = [i for i in sorted(ids) if m.idmap.get(sm, i)]
        print("%-22s %d/%d map ho gaye" % (rel, len(ok), len(ids)))
        for i in sorted(ids):
            if i in ok:
                continue
            bad += 1
            print("   NAHI HUA: %s#%s  %s" % (rel, i, names.get(i, "")))
            for h in reasons(sm, i):
                print("        log: %s" % h)
    if bad:
        print("\n%d ids abhi bhi map nahi hue - upar ki log lines yahan bhej do." % bad)
    else:
        print("\nSab map ho gaye. Ab: python3 migrate_transactions_ext.py run --steps sales")


if __name__ == "__main__":
    main()
