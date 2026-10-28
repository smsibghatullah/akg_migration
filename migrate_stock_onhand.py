#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Odoo 16 -> Odoo 20: products ki ON-HAND quantity (stock.quant, internal locations)

  python3 migrate_stock_onhand.py run --dry-run             # kuch likhe baghair report
  python3 migrate_stock_onhand.py run --limit 5             # sirf 5 (product, location) ke liye
  python3 migrate_stock_onhand.py run --workers 2
  python3 migrate_stock_onhand.py verify                    # product-wise source vs destination total

Kaise kaam karta hai:
  * Source ki har (product, location, lot) ki quantity jama karta hai (sirf internal locations).
  * Destination mein wahi quantity "Inventory Adjustment" (inventory_quantity + action_apply_inventory) se set karta hai.
  * Pehle se barabar ho to skip (dobara chalana safe hai). Destination mein pehle se alag quantity ho to source ke barabar kar deta hai.
  * Masters (product, location) migrate_transactions wale hi map (idmap.json / txmap.sqlite3) se lagte hain.
  * Report: stock_onhand_report.csv

DHYAN: agar product categories ki valuation "Automated" hai to har adjustment ka accounting entry banega.
Journal entries 'moves' step mein pehle hi aa chuki hain, to double counting ho sakti hai. Pehle --limit 5 se test karo.
"""
import sys
import csv
import argparse
import logging
import threading
import collections
from concurrent.futures import ThreadPoolExecutor

import migrate_transactions as mt

log = mt.log
INV_CTX = {"inventory_mode": True, "tracking_disable": True, "mail_notrack": True, "active_test": False}
READ_CTX = {"active_test": False}

RESULTS, _lock = [], threading.Lock()
_prod_cache, _lot_cache, _company = {}, {}, [None]
LOT_SRC = LOT_DST = None


def add(item, result, detail=""):
    with _lock:
        RESULTS.append((item[0], item[1], item[2], item[3], result, detail))


def lot_models():
    global LOT_SRC, LOT_DST
    for m in ("stock.lot", "stock.production.lot"):
        if LOT_SRC is None and mt.src.fields(m) is not None:
            LOT_SRC = m
        if LOT_DST is None and mt.dst.fields(m) is not None:
            LOT_DST = m


def aggregate_src():
    sf = mt.src.fields("stock.quant")
    fl = [f for f in ("product_id", "location_id", "lot_id", "quantity") if f in sf]
    agg, off = collections.OrderedDict(), 0
    while True:
        part = mt.src.call("stock.quant", "search_read", [("location_id.usage", "=", "internal")], fields=fl,
                           limit=5000, offset=off, order="id", context=READ_CTX)
        for r in part:
            key = (mt.m2o_id(r["product_id"]), mt.m2o_id(r["location_id"]), mt.m2o_id(r.get("lot_id")) or 0)
            agg[key] = agg.get(key, 0.0) + (r.get("quantity") or 0.0)
        if len(part) < 5000:
            break
        off += 5000
    return agg


def dst_company():
    with _lock:
        if _company[0] is None:
            r = mt.dst.call("res.company", "search", [], context=READ_CTX)
            _company[0] = r[0] if r else False
        return _company[0]


def get_lot(src_lot, dst_product, dry):
    """Source lot ka naam + product se destination lot dhundta hai, na mile to banata hai. (id, note) return karta hai."""
    key = (src_lot, dst_product)
    with _lock:
        if key in _lot_cache:
            return _lot_cache[key], ""
    if not (LOT_SRC and LOT_DST):
        return None, "lot model nahi mila"
    s = mt.src.call(LOT_SRC, "read", [src_lot], fields=["name", "company_id"], context=READ_CTX)[0]
    found = mt.dst.call(LOT_DST, "search", [("name", "=", s["name"]), ("product_id", "=", dst_product)], context=READ_CTX)
    if found:
        lid = found[0]
    elif dry:
        return -1, "lot banana padega"
    else:
        comp = mt.resolve("res.company", mt.m2o_id(s.get("company_id"))) or dst_company()
        vals = {"name": s["name"], "product_id": dst_product}
        if comp:
            vals["company_id"] = comp
        lid = mt.dst.call(LOT_DST, "create", vals, context=INV_CTX)
    with _lock:
        _lot_cache[key] = lid
    return lid, ""


def product_info(dps, make_storable):
    """Destination products: storable? tracking? (cache ke saath)"""
    dfp = mt.dst.fields("product.product") or {}
    need = [p for p in dps if p not in _prod_cache]
    if need:
        fl = [f for f in ("is_storable", "type", "tracking", "product_tmpl_id") if f in dfp]
        for r in mt.dst.call("product.product", "read", need, fields=fl, context=READ_CTX):
            storable = r["is_storable"] if "is_storable" in r else r.get("type") == "product"
            if not storable and make_storable:
                tf = mt.dst.fields("product.template") or {}
                tmpl = mt.m2o_id(r["product_tmpl_id"])
                if "is_storable" in tf:
                    mt.dst.call("product.template", "write", [tmpl], {"is_storable": True}, context=READ_CTX)
                    storable = True
                elif "type" in tf:
                    mt.dst.call("product.template", "write", [tmpl], {"type": "product"}, context=READ_CTX)
                    storable = True
            with _lock:
                _prod_cache[r["id"]] = (storable, r.get("tracking") or "none")
    return {p: _prod_cache.get(p, (False, "none")) for p in dps}


def process(chunk, a):
    rows = []
    for item in chunk:
        sp, sl, slot, qty = item
        dp, dl = mt.resolve("product.product", sp), mt.resolve("stock.location", sl)
        if not dp:
            add(item, "SKIP", "product map nahi (product.product#%s)" % sp)
        elif not dl:
            add(item, "SKIP", "location map nahi (stock.location#%s)" % sl)
        else:
            rows.append((item, dp, dl))
    if not rows:
        return
    info = product_info(sorted({r[1] for r in rows}), a.make_storable)
    ready = []
    for item, dp, dl in rows:
        storable, tracking = info[dp]
        lot = 0
        if not storable:
            add(item, "SKIP", "destination mein product storable nahi (--make-storable se on kar sakte ho)")
            continue
        if tracking in ("lot", "serial"):
            if not item[2]:
                add(item, "SKIP", "tracked product hai lekin source quant mein lot nahi")
                continue
            try:
                lot, note = get_lot(item[2], dp, a.dry_run)
            except Exception as e:
                add(item, "ERROR", "lot: " + str(e)[:200])
                continue
            if not lot:
                add(item, "SKIP", note)
                continue
        ready.append((item, dp, dl, lot))
    if not ready:
        return
    existing = collections.defaultdict(list)
    dom = [("product_id", "in", sorted({r[1] for r in ready})), ("location_id", "in", sorted({r[2] for r in ready}))]
    for q in mt.dst.call("stock.quant", "search_read", dom, fields=["product_id", "location_id", "lot_id", "quantity"],
                         context=READ_CTX):
        existing[(mt.m2o_id(q["product_id"]), mt.m2o_id(q["location_id"]), mt.m2o_id(q.get("lot_id")) or 0)].append(q)
    to_apply = {}
    for item, dp, dl, lot in ready:
        qty = item[3]
        ex = existing.get((dp, dl, lot if lot and lot > 0 else 0), [])
        cur = sum(e.get("quantity") or 0.0 for e in ex)
        if abs(cur - qty) < 0.00005:
            add(item, "ALREADY", "dst quantity %s" % cur)
            continue
        if len(ex) > 1:
            add(item, "SKIP", "destination mein is (product, location, lot) ke %d quants hain, haath se dekho" % len(ex))
            continue
        if a.dry_run:
            add(item, "DRY_OK", "dst %s -> %s" % (cur, qty))
            continue
        try:
            if ex:
                qid = ex[0]["id"]
                mt.dst.call("stock.quant", "write", [qid], {"inventory_quantity": qty}, context=INV_CTX)
            else:
                vals = {"product_id": dp, "location_id": dl, "inventory_quantity": qty}
                if lot and lot > 0:
                    vals["lot_id"] = lot
                qid = mt.dst.call("stock.quant", "create", vals, context=INV_CTX)
            to_apply[qid] = (item, cur)
        except Exception as e:
            add(item, "ERROR", "quant: " + str(e)[:250])
    if not to_apply:
        return
    try:
        mt.dst.call("stock.quant", "action_apply_inventory", ids=list(to_apply), context=INV_CTX)
        for qid, (item, cur) in to_apply.items():
            add(item, "OK", "dst %s -> %s" % (cur, item[3]))
    except Exception as e:
        log.warning("batch apply fail (%s) - ek ek karke koshish", str(e)[:150])
        for qid, (item, cur) in to_apply.items():
            try:
                mt.dst.call("stock.quant", "action_apply_inventory", ids=[qid], context=INV_CTX)
                add(item, "OK", "dst %s -> %s" % (cur, item[3]))
            except Exception as e2:
                add(item, "ERROR", "apply: " + str(e2)[:250])


def cmd_run(a):
    lot_models()
    agg = aggregate_src()
    items, neg = [], 0
    for (p, l, lot), q in agg.items():
        if abs(q) < 0.00005:
            continue
        if q < 0 and not a.include_negative:
            neg += 1
            add((p, l, lot, q), "SKIP", "source mein negative quantity (--include-negative se bhi daal sakte ho)")
            continue
        items.append((p, l, lot, q))
    if a.limit:
        items = items[:a.limit]
    log.info("source (product, location, lot) rows: %d | negative skip: %d | process karne hain: %d%s",
             len(agg), neg, len(items), "  [DRY-RUN]" if a.dry_run else "")
    chunks = [items[i:i + a.chunk] for i in range(0, len(items), a.chunk)]
    done = [0]

    def task(ch):
        try:
            process(ch, a)
        except Exception as e:
            log.error("chunk fail: %s", str(e)[:300])
            for it in ch:
                add(it, "ERROR", "chunk: " + str(e)[:250])
        with _lock:
            done[0] += len(ch)
            log.info("progress %d/%d", done[0], len(items))

    if a.workers <= 1:
        for ch in chunks:
            task(ch)
    else:
        with ThreadPoolExecutor(max_workers=a.workers) as ex:
            list(ex.map(task, chunks))
    with open("stock_onhand_report.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["src_product", "src_location", "src_lot", "quantity", "result", "details"])
        w.writerows(RESULTS)
    log.info("Report: stock_onhand_report.csv | %s", dict(collections.Counter(r[4] for r in RESULTS)))


def totals(conn, product_map=None):
    tot, off = collections.defaultdict(float), 0
    while True:
        part = conn.call("stock.quant", "search_read", [("location_id.usage", "=", "internal")],
                         fields=["product_id", "quantity"], limit=5000, offset=off, order="id", context=READ_CTX)
        for r in part:
            tot[mt.m2o_id(r["product_id"])] += r.get("quantity") or 0.0
        if len(part) < 5000:
            break
        off += 5000
    return tot


def cmd_verify(a):
    s, d = totals(mt.src), totals(mt.dst)
    bad, unmapped = [], 0
    for sp, sq in s.items():
        if abs(sq) < 0.00005:
            continue
        dp = mt.resolve("product.product", sp)
        if not dp:
            unmapped += 1
            continue
        dq = d.get(dp, 0.0)
        if abs(dq - sq) > 0.0005:
            bad.append((sp, dp, sq, dq))
    print("source products (qty != 0): %d | map nahi: %d | quantity mein farq: %d" % (
        sum(1 for q in s.values() if abs(q) > 0.00005), unmapped, len(bad)))
    for b in bad[:20]:
        print("  src product#%s dst#%s  source %s | destination %s" % b)
    with open("stock_onhand_verify.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["src_product", "dst_product", "src_qty", "dst_qty"])
        w.writerows(bad)
    print("poori list: stock_onhand_verify.csv")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["run", "verify"])
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--chunk", type=int, default=100)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--include-negative", action="store_true")
    ap.add_argument("--make-storable", action="store_true",
                    help="destination mein jo product storable nahi unhe storable kar do (product config badalta hai)")
    a = ap.parse_args()
    mt.src, mt.dst, mt.MAP = mt.Src(), mt.Dst(), mt.IdMap()
    try:
        cmd_run(a) if a.cmd == "run" else cmd_verify(a)
    finally:
        mt.MAP.commit()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit("Stopped by user.")
    except Exception as exc:
        log.exception("Stopped")
        sys.exit(str(exc))
