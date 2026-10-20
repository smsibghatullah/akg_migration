#!/usr/bin/env python3
"""
Odoo 16 -> Odoo 20 : PRODUCTS MIGRATION  (alag script, sirf products ke liye)

Ye script odoo16_to_20_migrate.py ke engine (connections, idmap.json, field mapping) ko hi use karti hai,
isliye dono SAME folder mein rakho aur idmap.json share hoga. Credentials wahi (local_config.py ya script ke upar).

COMMANDS
  python3 migrate_products.py check                 # READ-ONLY audit: kya kya masla aayega (pehle ye chalao)
  python3 migrate_products.py fix-merged [--dry-run]  # jo source products aik dst record par merge ho gaye unhe alag karo
  python3 migrate_products.py run --dry-run         # kuch likhe baghair poora plan
  python3 migrate_products.py run                   # poori products migration (sab stages)
  python3 migrate_products.py run --stage templates,variants     # sirf kuch stages
  python3 migrate_products.py verify                # sach mein Odoo 20 mein kya hai (counts + per-record check)

STAGES (is tarteeb se)
  base       uom, product category, POS category, attributes + values, eCommerce category, ribbons
  vendors    jin partners ko supplierinfo (vendor prices) use karti hai, sirf wahi partners
  fix        merge hue products alag (auto)
  templates  product.template + attribute lines + attribute extra price
  variants   product.product (default_code, barcode, variant values)
  supplier   vendor prices (product.supplierinfo) - duplicate se bachao ke saath
  pricelists pricelists + pricelist rules
  extras     reorder rules, BoM (mrp.bom + lines), lot/serial (stock.lot)
  verify     end mein verification report

Har stage dobara chalana safe hai. Output files:
  products_check.txt, products_field_loss.csv, products_verify.csv, products_verify_variants.csv,
  products_unmigrated_other.csv, products_verify_summary.txt, barcode_conflicts.csv,
  dropped_values.csv (engine), unresolved.csv (engine), migration.log (engine)

NOTE: Stock quantities (stock.quant / opening stock) aur stock valuation is script mein NAHI hain - wo alag decision hai.
"""
import argparse
import collections
import csv
import os
import re
import shutil
import sys
import time

import odoo16_to_20_migrate as m

log = m.log
CTX = m.CTX

STAGES = ["base", "vendors", "fix", "templates", "variants", "supplier", "pricelists", "extras", "verify"]

# ----------------------------------------------------------------------------- helpers


def connect():
    m.src, m.dst = m.Odoo(m.SRC), m.Odoo2(m.DST)
    m.idmap = m.IdMap(m.IDMAP_FILE)


def norm(x):
    return re.sub(r"\s+", " ", str(x or "").strip()).lower()


def count_errors():
    try:
        with open("migration.log", errors="ignore") as fh:
            return sum(1 for line in fh if " ERROR " in line)
    except FileNotFoundError:
        return 0


def read_src(model, ids, fields, chunk=300):
    out = []
    for i in range(0, len(ids), chunk):
        out += m.src.call(model, "read", ids[i:i + chunk], fields=fields, context=CTX)
    return out


def read_dst(model, ids, fields, chunk=300):
    """dst par 'search_read id in' (jo ids maujood nahi wo khud nikal jate hain, error nahi aata)"""
    out = []
    for i in range(0, len(ids), chunk):
        out += m.dst.call(model, "search_read", [("id", "in", ids[i:i + chunk])], fields=fields, context=CTX)
    return out


def present(fields_dict, names):
    return [n for n in names if fields_dict and n in fields_dict]


def write_csv(path, header, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)


def emit(lines, path=None):
    txt = "\n".join(lines)
    print(txt)
    if path:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(txt + "\n")


def backup_idmap(tag):
    if os.path.exists(m.IDMAP_FILE):
        dst = "%s.%s_%s" % (m.IDMAP_FILE, tag, time.strftime("%Y%m%d_%H%M%S"))
        shutil.copy(m.IDMAP_FILE, dst)
        print("idmap backup: %s" % dst)


_orig_flush = m.flush_pending
_route_warned = set()


def prefilter_routes():
    """Odoo 20 mein route_ids par domain hai: sirf product_selectable=True routes link ho sakte hain.
    Baqi routes har template par fail ho kar waqt aur API calls zaya karte hain, isliye pehle hi hata do."""
    rf = m.dst.fields("stock.route") or {}
    if "product_selectable" not in rf or not m.PENDING:
        return
    bad = set(m.dst.call("stock.route", "search", [("product_selectable", "=", False)], context=CTX))
    if not bad:
        return
    for k, (dm, did, f, t, rel, sv) in enumerate(m.PENDING):
        if f == "route_ids" and t == "many2many":
            keep = [i for i in sv if m.resolve(rel, i) not in bad]
            skipped = {m.resolve(rel, i) for i in sv} & bad
            for r in skipped - _route_warned:
                _route_warned.add(r)
                log.warning("stock.route dst id %s 'Applicable on Product' band hai - products par link NAHI hoga. "
                            "Chahiye to Odoo 20 mein Inventory > Configuration > Routes mein ON karke templates "
                            "stage dobara chalao.", r)
            if len(keep) != len(sv):
                m.PENDING[k] = (dm, did, f, t, rel, keep)


def flush_pending(final=False):
    prefilter_routes()
    return _orig_flush(final)


m.flush_pending = flush_pending


def run_models(models, domain=None):
    """engine ke STEPS mein se in models ke steps (STEPS ki apni tarteeb mein) chalao"""
    wanted = set(models)
    prev = m.DOMAIN_OVERRIDE
    m.DOMAIN_OVERRIDE = domain
    try:
        for st in m.STEPS:
            if st["model"] in wanted:
                m.run_step(st)
        m.flush_pending(final=True)
    finally:
        m.DOMAIN_OVERRIDE = prev
    m.idmap.save()


def src_count(name):
    sm = m.src_model(name)
    if not sm:
        return None
    return m.src.call(sm, "search_count", [], context=CTX)


def dst_count(name):
    dm = m.dst_model(name)
    if not dm:
        return None
    return m.dst.call(dm, "search_count", [], context=CTX)


# ----------------------------------------------------------------------------- merged-products fix


def find_shared(model="product.template"):
    mp = m.idmap.d.get(model, {})
    by = collections.defaultdict(list)
    for s, d in mp.items():
        by[d].append(int(s))
    return {d: sorted(v) for d, v in by.items() if len(v) > 1}


def fix_merged(apply):
    """Aik dst template par jin source templates ne merge kiya (same naam+code), unme se aik 'malik' rakho
    (ACTIVE wala, warna sab se chota id), baqi ke idmap entries (aur unke variants/attribute lines) hata do.
    Dobara migrate par baqi ke liye NAYE alag records bante hain aur malik ka active/archived wapis theek hota hai."""
    groups = find_shared("product.template")
    if not groups:
        print("product.template: koi shared dst record nahi - fix ki zaroorat nahi.")
        return 0
    allsrc = sorted({i for v in groups.values() for i in v})
    info = {r["id"]: r for r in read_src("product.template", allsrc, ["name", "default_code", "active"])}
    drop, keep = [], []
    for d, ids in groups.items():
        owner = sorted(ids, key=lambda i: (not info.get(i, {}).get("active", False), i))[0]
        keep.append(owner)
        drop += [i for i in ids if i != owner]
    print("product.template: %d shared dst records, %d source templates alag kiye jayenge:" % (len(groups), len(drop)))
    for i in drop[:15]:
        r = info.get(i, {})
        print("     src %-6s active=%-5s code=%-12s %s" % (i, r.get("active"), r.get("default_code") or "-", r.get("name")))
    if len(drop) > 15:
        print("     ... aur %d" % (len(drop) - 15))
    if not apply:
        print("(dry-run: kuch badla nahi)")
        return len(drop)
    backup_idmap("before_fix")
    mp = m.idmap.d["product.template"]
    for i in drop:
        mp.pop(str(i), None)
    extra = 0
    for child, fld in (("product.product", "product_tmpl_id"), ("product.template.attribute.line", "product_tmpl_id")):
        if m.src_model(child):
            cmp_ = m.idmap.d.get(child, {})
            for j in range(0, len(drop), 200):
                for cid in m.src.call(child, "search", [(fld, "in", drop[j:j + 200])], context=CTX):
                    if cmp_.pop(str(cid), None) is not None:
                        extra += 1
    m.idmap.save()
    print("%d template mappings hata di, %d variants/lines mappings bhi. Ab 'templates' stage chalao." % (len(drop), extra))
    return len(drop)


# ----------------------------------------------------------------------------- supplierinfo (vendor prices)

_tmpl_uom = {}


def prefetch_template_uoms(tmpl_ids):
    ids = sorted({i for i in tmpl_ids if i and i not in _tmpl_uom})
    sf = m.src.fields("product.template") or {}
    names = present(sf, ["uom_po_id", "uom_id"])
    for r in read_src("product.template", ids, names) if names else []:
        po = m.m2o_id(r.get("uom_po_id")) if r.get("uom_po_id") else None
        base = m.m2o_id(r.get("uom_id")) if r.get("uom_id") else None
        _tmpl_uom[r["id"]] = m.resolve("uom.uom", po or base)


def find_supplierinfo(rec):
    """Pehle se dst mein wahi vendor-price (partner+template+min_qty+price) ho to naya mat banao"""
    t = m.resolve("product.template", m.m2o_id(rec["product_tmpl_id"])) if rec.get("product_tmpl_id") else None
    p = m.resolve("res.partner", m.m2o_id(rec["partner_id"])) if rec.get("partner_id") else None
    if not (t and p):
        return None
    dom = [("product_tmpl_id", "=", t), ("partner_id", "=", p),
           ("min_qty", "=", rec.get("min_qty") or 0.0), ("price", "=", rec.get("price") or 0.0)]
    if rec.get("product_code"):
        dom.append(("product_code", "=", rec["product_code"]))
    used = set(m.idmap.d.get("product.supplierinfo", {}).values())
    for d in m.dst.call("product.supplierinfo", "search", dom, context=CTX):
        if d not in used:
            return d
    return None


def h_supplierinfo(vals, rec, later):
    if not vals.get("product_tmpl_id") or not vals.get("partner_id"):
        log.warning("supplierinfo %s: template ya vendor abhi migrate nahi hua - skip (stage vendors/templates dobara)",
                    rec.get("id"))
        return None
    df = m.dst.fields("product.supplierinfo") or {}
    if "uom_id" in df and not vals.get("uom_id"):          # Odoo 20 mein uom_id zaroori hai
        tid = m.m2o_id(rec["product_tmpl_id"]) if rec.get("product_tmpl_id") else None
        if tid not in _tmpl_uom:
            prefetch_template_uoms([tid])
        if _tmpl_uom.get(tid):
            vals["uom_id"] = _tmpl_uom[tid]
    return vals


def stage_supplier():
    sm = m.src_model("product.supplierinfo")
    if not sm:
        print("product.supplierinfo Odoo16 mein nahi")
        return
    rows = m.src.call(sm, "search_read", [], fields=["product_tmpl_id"], context=CTX)
    prefetch_template_uoms([m.m2o_id(r["product_tmpl_id"]) for r in rows if r.get("product_tmpl_id")])
    base = next(s for s in m.STEPS if s["model"] == "product.supplierinfo")
    st = dict(base)
    st.update(finder=find_supplierinfo, hook=h_supplierinfo,
              extra_read=["product_tmpl_id", "partner_id", "min_qty", "price", "product_code"])
    m.run_step(st)
    m.flush_pending(final=True)
    m.idmap.save()


# ----------------------------------------------------------------------------- vendors


def stage_vendors():
    sm = m.src_model("product.supplierinfo")
    if not sm:
        return
    rows = m.src.call(sm, "search_read", [], fields=["partner_id"], context=CTX)
    ids = sorted({m.m2o_id(r["partner_id"]) for r in rows if r.get("partner_id")})
    missing = [i for i in ids if not m.resolve("res.partner", i)]
    print("vendors: %d partners supplierinfo mein, %d abhi migrate nahi hue" % (len(ids), len(missing)))
    if not missing:
        return
    run_models(["res.country.state", "res.partner.category", "res.partner.industry"])
    run_models(["res.partner"], domain=[("id", "in", missing)])


# ----------------------------------------------------------------------------- attribute extra price


def fix_price_extra():
    sm = "product.template.attribute.value"
    sf, df = m.src.fields(sm), m.dst.fields(sm)
    if not sf or not df or "price_extra" not in sf or "price_extra" not in df:
        return
    rows = m.src.call(sm, "search_read", [("price_extra", "!=", 0)],
                      fields=["product_tmpl_id", "product_attribute_value_id", "price_extra"], context=CTX)
    done = 0
    for r in rows:
        t = m.resolve("product.template", m.m2o_id(r["product_tmpl_id"]))
        v = m.resolve("product.attribute.value", m.m2o_id(r["product_attribute_value_id"]))
        if not (t and v):
            log.warning("attribute extra price: template/value migrate nahi hui (src ptav %s)", r["id"])
            continue
        f = m.dst.call(sm, "search", [("product_tmpl_id", "=", t), ("product_attribute_value_id", "=", v)],
                       limit=1, context=CTX)
        if not f:
            log.warning("attribute extra price: dst par ptav nahi mili (template %s value %s)", t, v)
            continue
        if not m.DRY:
            try:
                m.dst.call(sm, "write", [f[0]], {"price_extra": r["price_extra"]}, context=CTX)
                done += 1
            except Exception as e:
                log.error("attribute extra price write fail ptav %s: %s", f[0], str(e)[:200])
        else:
            done += 1
    print("attribute extra price: %d / %d set" % (done, len(rows)))


# ----------------------------------------------------------------------------- extras

EXTRA_STEPS = [
    dict(model="mrp.bom", key=["product_tmpl_id", "code", "type"], extra_read=["product_tmpl_id", "product_id"]),
    dict(model="mrp.bom.line", key=["bom_id", "product_id"]),
    dict(model="stock.lot", key=["name", "product_id", "company_id"]),
]


def stage_extras():
    run_models(["stock.warehouse.orderpoint"])
    for st in EXTRA_STEPS:
        n = src_count(st["model"])
        if not n:
            print("%s: Odoo16 mein nahi / 0 records - skip" % st["model"])
            continue
        m.run_step(st)
    m.flush_pending(final=True)
    m.idmap.save()
    n = src_count("product.packaging")
    if n:
        print("WARNING: product.packaging mein %d records hain - Odoo 20 mein packaging ka structure badal gaya, "
              "ye manual/alag handle karna hoga." % n)


# ----------------------------------------------------------------------------- run


def with_errors(name, fn):
    print("\n================ %s ================" % name)
    before = count_errors()
    fn()
    new = count_errors() - before
    if new > 0:
        print(">>> %d NAYE ERRORS. Aakhri 5:" % new)
        with open("migration.log", errors="ignore") as fh:
            errs = [x.rstrip()[:260] for x in fh if " ERROR " in x]
        for x in errs[-5:]:
            print("    " + x)
    else:
        print(">>> OK (naye errors: 0)")


def cmd_run(a):
    connect()
    m.DRY = a.dry_run
    m.seed_companies()
    stages = STAGES if a.stage in (None, "all") else [s.strip() for s in a.stage.split(",")]
    bad = [s for s in stages if s not in STAGES]
    if bad:
        raise SystemExit("ghalat stage: %s  (sahi: %s)" % (bad, ", ".join(STAGES)))
    if not a.dry_run:
        backup_idmap("products")
    for s in STAGES:
        if s not in stages:
            continue
        if s == "base":
            with_errors("base: uom / categories / attributes / eCommerce", lambda: run_models(
                ["uom.uom", "product.category", "pos.category", "product.attribute", "product.attribute.value",
                 "product.public.category", "product.ribbon"]))
        elif s == "vendors":
            with_errors("vendors", stage_vendors)
        elif s == "fix":
            if a.no_fix_merged:
                print("\n(fix stage skip: --no-fix-merged)")
            else:
                with_errors("fix: merge hue products alag karo", lambda: fix_merged(apply=not a.dry_run))
        elif s == "templates":
            def _t():
                run_models(["product.template", "product.template.attribute.line"])
                fix_price_extra()
            with_errors("templates + attribute lines", _t)
        elif s == "variants":
            with_errors("variants", lambda: run_models(["product.product"]))
        elif s == "supplier":
            with_errors("supplier prices", stage_supplier)
        elif s == "pricelists":
            def _p():
                run_models(["product.pricelist"])
                run_models(["product.pricelist.item"])
            with_errors("pricelists + rules", _p)
        elif s == "extras":
            with_errors("extras: reorder rules, BoM, lots", stage_extras)
        elif s == "verify":
            if a.dry_run:
                print("\n(verify dry-run mein skip)")
            else:
                cmd_verify(a, connected=True)
    print("\nKHATAM. products_verify_summary.txt dekho (verify stage), aur unresolved.csv / dropped_values.csv.")


# ----------------------------------------------------------------------------- check (read-only)

REL_CHECKS = [
    ("product.template", [("categ_id", "product.category"), ("uom_id", "uom.uom"), ("uom_po_id", "uom.uom"),
                          ("pos_categ_id", "pos.category"), ("taxes_id", "account.tax"),
                          ("supplier_taxes_id", "account.tax"), ("route_ids", "stock.route"),
                          ("public_categ_ids", "product.public.category")]),
    ("product.supplierinfo", [("partner_id", "res.partner")]),
    ("product.pricelist.item", [("categ_id", "product.category")]),
]
HANDLED = {"detailed_type", "pos_categ_id", "uom_po_id", "priority", "expense_policy", "lst_price", "write_date",
           "create_date", "create_uid", "write_uid", "id", "display_name", "__last_update"}
LOSS_MODELS = ["product.template", "product.product", "product.supplierinfo", "product.pricelist.item",
               "product.pricelist", "product.category"]
CHECK_MODELS = ["uom.uom", "product.category", "pos.category", "product.attribute", "product.attribute.value",
                "product.public.category", "product.ribbon", "product.template", "product.template.attribute.line",
                "product.product", "product.supplierinfo", "product.pricelist", "product.pricelist.item",
                "stock.warehouse.orderpoint", "mrp.bom", "mrp.bom.line", "stock.lot", "product.packaging",
                "product.image", "product.tag"]


def field_loss():
    rows = []
    for name in LOSS_MODELS:
        sm, dm = m.src_model(name), m.dst_model(name)
        if not sm or not dm:
            continue
        sf, df = m.src.fields(sm), m.dst.fields(dm)
        for f, s in sorted(sf.items()):
            if f in HANDLED or f.startswith(("message_", "activity_", "website_message", "image_", "rating_")):
                continue
            if s["type"] in ("one2many", "binary") and f != "image_1920":
                continue
            if not (s.get("store") or s.get("company_dependent")):
                continue
            d = df.get(f)
            if d is None:
                status = "REMOVED_IN_20"
            elif d["type"] != s["type"]:
                status = "TYPE_CHANGED %s->%s" % (s["type"], d["type"])
            else:
                continue
            try:
                n = m.src.call(sm, "search_count", [(f, "!=", False)], context=CTX)
            except Exception:
                n = "?"
            if n:
                rows.append((name, f, s.get("string"), s["type"], status, n))
    write_csv("products_field_loss.csv", ["model", "field", "label", "type", "status", "src_records_with_value"], rows)
    return rows


def cmd_check(a):
    connect()
    out = ["PRODUCTS PRE-CHECK (read-only)", ""]
    out.append("%-34s %8s %8s %8s %9s  %s" % ("model", "odoo16", "odoo20", "mapped", "uniq_dst", "note"))
    for name in CHECK_MODELS:
        sm, dm = m.src_model(name), m.dst_model(name)
        n16 = m.src.call(sm, "search_count", [], context=CTX) if sm else None
        n20 = m.dst.call(dm, "search_count", [], context=CTX) if dm else None
        mp = m.idmap.d.get(sm or name, {})
        note = []
        if sm is None:
            note.append("Odoo16 mein model nahi")
        elif dm is None and n16:
            note.append("Odoo20 mein MODEL NAHI (app install karo)")
        if mp and len(mp) > len(set(mp.values())):
            note.append("SHARED_DST %d" % (len(mp) - len(set(mp.values()))))
        if sm and n16 and len(mp) < n16 and dm:
            note.append("unmapped ~%d" % (n16 - len(mp)))
        out.append("%-34s %8s %8s %8s %9s  %s" % (name, n16 if n16 is not None else "-", n20 if n20 is not None else "-",
                                                  len(mp), len(set(mp.values())), "; ".join(note)))
    # duplicates in source
    out += ["", "SOURCE DUPLICATES (same naam + default_code) - product.template"]
    recs = m.src.call("product.template", "search_read", [], fields=["name", "default_code", "active"], context=CTX)
    g = collections.defaultdict(list)
    for r in recs:
        g[(norm(r["name"]), norm(r.get("default_code") or ""))].append(r)
    dups = {k: v for k, v in g.items() if len(v) > 1}
    both = sum(1 for v in dups.values() if any(x["active"] for x in v) and any(not x["active"] for x in v))
    out.append("  %d groups, jin mein %d groups mein active + archived dono hain" % (len(dups), both))
    for k, v in list(dups.items())[:10]:
        out.append("   %s | ids %s | active %s" % (v[0]["name"][:50], [x["id"] for x in v], [x["active"] for x in v]))
    out.append("  (fix-merged / run ka 'fix' stage inhe alag records banata hai)")
    # unmapped dependencies
    out += ["", "DEPENDENCIES (jo parent records abhi map nahi hue)"]
    for model, rels in REL_CHECKS:
        sm = m.src_model(model)
        if not sm:
            continue
        sf = m.src.fields(sm)
        fl = [f for f, _ in rels if f in sf]
        if not fl:
            continue
        data = m.src.call(sm, "search_read", [], fields=fl, context=CTX)
        for f, rel in rels:
            if f not in sf:
                continue
            ids = set()
            for r in data:
                v = r.get(f)
                if isinstance(v, list) and v and isinstance(v[0], int) and sf[f]["type"] == "many2many":
                    ids.update(v)
                elif v:
                    ids.add(m.m2o_id(v))
            miss = sorted(i for i in ids if not m.resolve(rel, i))
            out.append("  %-34s -> %-22s %5d distinct, %d unmapped %s" % (
                model + "." + f, rel, len(ids), len(miss), miss[:8] if miss else ""))
    out.append("  (unmapped ho to pehle ./run_migration.sh 1 (taxes/accounts), 7 (routes) chalao)")
    # barcode conflicts in source
    out += ["", "BARCODE CONFLICTS (source mein aik barcode aik se zyada variants par)"]
    sfp = m.src.fields("product.product") or {}
    if "barcode" in sfp:
        vr = m.src.call("product.product", "search_read", [("barcode", "!=", False)],
                        fields=["barcode", "default_code", "display_name", "active"], context=CTX)
        gb = collections.defaultdict(list)
        for r in vr:
            gb[r["barcode"].strip()].append(r)
        conf = {b: v for b, v in gb.items() if len(v) > 1}
        rows = [(b, r["id"], r.get("default_code") or "", r["display_name"], r["active"]) for b, v in conf.items() for r in v]
        write_csv("barcode_conflicts.csv", ["barcode", "src_product_id", "default_code", "name", "active"], rows)
        out.append("  %d barcodes duplicate -> barcode_conflicts.csv (inme se aik ko manually badalna hoga)" % len(conf))
    # field loss
    out += ["", "FIELDS JIN KA DATA ODOO 20 MEIN NAHI JAYEGA (products_field_loss.csv)"]
    loss = field_loss()
    for model, f, label, t, status, n in loss[:40]:
        out.append("  %-26s %-28s %-22s %s records" % (model, f, status[:22], n))
    if not loss:
        out.append("  koi nahi")
    emit(out, "products_check.txt")


# ----------------------------------------------------------------------------- verify


def _fdiff(x, y):
    return abs((x or 0.0) - (y or 0.0)) > 0.005


def cmd_verify(a, connected=False):
    if not connected:
        connect()
    out = ["PRODUCTS VERIFY", ""]
    sm, dm = "product.template", m.dst_model("product.template")
    sfl, dfl = m.src.fields(sm), m.dst.fields(dm)
    ws = present(sfl, ["name", "default_code", "active", "list_price", "categ_id", "uom_id", "detailed_type", "type",
                       "barcode", "sale_ok", "purchase_ok", "available_in_pos"])
    wd = present(dfl, ["name", "default_code", "active", "list_price", "categ_id", "uom_id", "type", "is_storable",
                       "barcode", "sale_ok", "purchase_ok", "available_in_pos"])
    src_ids = m.src.call(sm, "search", [], context=CTX)
    S = {r["id"]: r for r in read_src(sm, src_ids, ws)}
    mp = {int(k): v for k, v in m.idmap.d.get(sm, {}).items()}
    D = {r["id"]: r for r in read_dst(dm, sorted(set(mp.values())), wd)}
    shared = collections.Counter(mp.get(i) for i in S if mp.get(i))
    rows, c = [], collections.Counter()

    def stype(r):
        dt = r.get("detailed_type") or r.get("type")
        return "storable" if dt == "product" else dt

    def dtype(r):
        if r.get("type") == "consu":
            return "storable" if r.get("is_storable") else "consu"
        return r.get("type")

    for sid in sorted(S):
        s = S[sid]
        did = mp.get(sid)
        issues = []
        d = D.get(did) if did else None
        if not did:
            issues.append("NOT_MIGRATED")
        elif not d:
            issues.append("DST_RECORD_MISSING")
        else:
            if shared[did] > 1:
                issues.append("SHARED_DST")
            if bool(s.get("active")) != bool(d.get("active")):
                issues.append("ACTIVE_MISMATCH")
            if norm(s.get("name")) != norm(d.get("name")):
                issues.append("NAME_MISMATCH")
            if norm(s.get("default_code") or "") != norm(d.get("default_code") or ""):
                issues.append("CODE_MISMATCH")
            if _fdiff(s.get("list_price"), d.get("list_price")):
                issues.append("PRICE_MISMATCH")
            if s.get("categ_id") and m.resolve("product.category", m.m2o_id(s["categ_id"])) != m.m2o_id(d.get("categ_id")):
                issues.append("CATEGORY_MISMATCH")
            if s.get("uom_id") and m.resolve("uom.uom", m.m2o_id(s["uom_id"])) != m.m2o_id(d.get("uom_id")):
                issues.append("UOM_MISMATCH")
            if stype(s) != dtype(d):
                issues.append("TYPE_MISMATCH")
            for f in ("sale_ok", "purchase_ok"):
                if f in s and f in d and bool(s[f]) != bool(d[f]):
                    issues.append(f.upper() + "_MISMATCH")
        for i in issues:
            c[i] += 1
        if issues:
            rows.append((sid, s.get("default_code") or "", s.get("name"), s.get("active"), did or "",
                         d.get("active") if d else "", ";".join(issues)))
    write_csv("products_verify.csv", ["src_id", "default_code", "name", "src_active", "dst_id", "dst_active", "issues"], rows)

    def cnt(recs, pred):
        return sum(1 for r in recs if pred(r))
    sa = [r for r in S.values() if r.get("active")]
    da = [D[mp[i]] for i in S if mp.get(i) in D and D[mp[i]].get("active")]
    out.append("TEMPLATES   Odoo16 total %d (active %d, archived %d)" % (len(S), len(sa), len(S) - len(sa)))
    uniq = {mp[i] for i in S if mp.get(i) in D}
    out.append("            Odoo20 (mapped, distinct) %d (active %d, archived %d)" % (
        len(uniq), len({x["id"] for x in da}), len(uniq) - len({x["id"] for x in da})))
    for f in ("sale_ok", "purchase_ok", "available_in_pos"):
        if f in ws and f in wd:
            out.append("   active + %-17s Odoo16 %5d   Odoo20 %5d" % (
                f, cnt(sa, lambda r: r.get(f)), cnt([D[u] for u in uniq if D[u].get("active")], lambda r: r.get(f))))
    out.append("   total active templates in Odoo20 (poora DB): %s" % m.dst.call(dm, "search_count", [("active", "=", True)], context=CTX))
    out.append("")
    out.append("ISSUES (products_verify.csv):")
    for k, v in sorted(c.items()):
        out.append("   %-24s %d" % (k, v))
    if not c:
        out.append("   koi nahi - sab templates theek")
    # variants
    pm, pdm = "product.product", m.dst_model("product.product")
    spf, dpf = m.src.fields(pm), m.dst.fields(pdm)
    vs = present(spf, ["default_code", "barcode", "active", "display_name"])
    vd = present(dpf, ["default_code", "barcode", "active"])
    vids = m.src.call(pm, "search", [], context=CTX)
    SV = {r["id"]: r for r in read_src(pm, vids, vs)}
    vmp = {int(k): v for k, v in m.idmap.d.get(pm, {}).items()}
    DV = {r["id"]: r for r in read_dst(pdm, sorted(set(vmp.values())), vd)}
    vrows, vc = [], collections.Counter()
    for vid in sorted(SV):
        s = SV[vid]
        did = vmp.get(vid)
        iss = []
        d = DV.get(did) if did else None
        if not did:
            iss.append("NOT_MIGRATED")
        elif not d:
            iss.append("DST_RECORD_MISSING")
        else:
            if bool(s.get("active")) != bool(d.get("active")):
                iss.append("ACTIVE_MISMATCH")
            if norm(s.get("default_code") or "") != norm(d.get("default_code") or ""):
                iss.append("CODE_MISMATCH")
            if (s.get("barcode") or "").strip() and not (d.get("barcode") or "").strip():
                iss.append("BARCODE_MISSING")
        for i in iss:
            vc[i] += 1
        if iss:
            vrows.append((vid, s.get("default_code") or "", s.get("display_name"), did or "", ";".join(iss)))
    write_csv("products_verify_variants.csv", ["src_variant_id", "default_code", "name", "dst_id", "issues"], vrows)
    out += ["", "VARIANTS    Odoo16 %d | mapped %d | Odoo20 distinct %d" % (len(SV), len(vmp), len(DV))]
    for k, v in sorted(vc.items()):
        out.append("   %-24s %d" % (k, v))
    if not vc:
        out.append("   koi issue nahi")
    # others
    other = []
    out.append("")
    for model in ("product.supplierinfo", "product.pricelist", "product.pricelist.item", "product.category",
                  "pos.category", "product.attribute", "product.attribute.value", "stock.warehouse.orderpoint",
                  "mrp.bom", "stock.lot"):
        sm_ = m.src_model(model)
        if not sm_:
            continue
        ids = m.src.call(sm_, "search", [], context=CTX)
        mp_ = m.idmap.d.get(sm_, {})
        um = [i for i in ids if str(i) not in mp_]
        dd = m.dst_model(model)
        exist = set(m.dst.call(dd, "search", [("id", "in", sorted(set(mp_.values())))], context=CTX)) if dd and mp_ else set()
        ghost = sum(1 for v in mp_.values() if v not in exist)
        out.append("%-30s Odoo16 %5d | unmapped %5d | mapped-but-missing-in-dst %d" % (model, len(ids), len(um), ghost))
        other += [(model, i) for i in um]
    write_csv("products_unmigrated_other.csv", ["model", "src_id"], other)
    out.append("")
    out.append("(unmapped = migrate nahi hua. pricelist.item mein archived rules jaan boojh kar skip hote hain - Odoo 20 mein 'active' nahi.)")
    emit(out, "products_verify_summary.txt")


# ----------------------------------------------------------------------------- main


def main():
    ap = argparse.ArgumentParser(description="Odoo16 -> Odoo20 products migration")
    ap.add_argument("cmd", choices=["check", "fix-merged", "run", "verify"])
    ap.add_argument("--stage", help="comma separated: " + ",".join(STAGES) + " (default: all)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-fix-merged", action="store_true", help="run mein auto 'fix' stage na chalao")
    a = ap.parse_args()
    if a.cmd == "check":
        return cmd_check(a)
    if a.cmd == "verify":
        return cmd_verify(a)
    if a.cmd == "fix-merged":
        connect()
        return fix_merged(apply=not a.dry_run)
    return cmd_run(a)


if __name__ == "__main__":
    main()