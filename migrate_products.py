#!/usr/bin/env python3
"""
Odoo 16 EE  ->  Odoo 20   TRANSACTIONAL DATA migration
(odoo16_to_20_migrate.py ka saathi script - usi folder mein rakho)

Ye script purane script ka engine use karti hai: wahi connections (SRC/DST ya local_config.py),
wahi idmap.json (master data), wahi migration.log.  Transaction ids alag file idmap_tx.json mein jati hain.

    python3 migrate_transactions.py list
    python3 migrate_transactions.py check                 # masters ka gap report + auto-map (kuch likhta nahi, sirf idmap)
    python3 migrate_transactions.py run --steps crm --limit 5 --dry-run
    python3 migrate_transactions.py run --steps crm,sales --limit 20      # chhota test
    python3 migrate_transactions.py run --steps core --workers 4          # sab core steps
    python3 migrate_transactions.py run --steps moves --date-from 2025-01-01 --workers 4
    python3 migrate_transactions.py verify                                # src vs dst ginti + trial balance

Har step dobara chalana SAFE hai (idmap_tx.json yaad rakhta hai, + dst mein naam se dhoondh kar adopt karta hai).
Fail hue records: tx_failed.csv   | chhode hue links: tx_dropped.csv   | total mismatch: tx_mismatch.csv
PEHLE STAGING / BACKUP PAR CHALAO.
"""
import os, sys, csv, json, time, random, argparse, threading, ast, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor

import odoo16_to_20_migrate as B

log = B.log
TX_IDMAP_FILE = "idmap_tx.json"
FAILED_CSV, DROPPED_CSV, MISMATCH_CSV = "tx_failed.csv", "tx_dropped.csv", "tx_mismatch.csv"
_lock = threading.Lock()
SRC_LOCK = threading.RLock()

# jin models ki ids idmap_tx.json mein jati hain (baqi purane idmap.json mein)
TX_MODELS = {
    "sale.order", "sale.order.line", "purchase.order", "purchase.order.line", "purchase.request",
    "purchase.request.line", "purchase.request.allocation", "account.move", "account.move.line",
    "account.payment", "account.bank.statement.line", "account.partial.reconcile",
    "account.full.reconcile", "account.analytic.line", "crm.lead", "hr.attendance", "hr.leave",
    "hr.leave.allocation", "hr.payslip", "hr.payslip.run", "hr.payslip.line", "hr.payslip.worked_days",
    "hr.payslip.input", "hr.expense", "hr.expense.sheet", "approval.request", "approval.approver",
    "approval.product.line", "pos.session", "pos.order", "pos.order.line", "pos.payment",
    "fleet.vehicle.odometer", "fleet.vehicle.log.contract", "fleet.vehicle.log.services",
}

# src relation -> dst relation (jahan Odoo 20 mein model badal gaya)
REL_ALIAS = {"hr.contract": "hr.version", "hr.leave.type": "hr.work.entry.type",
             "hr.payslip.input.type": "hr.salary.rule", "stock.location.route": "stock.route"}
# field renames (sirf tab lagte hain jab purana naam dst mein na ho)
RENAMES = {"product_uom": "product_uom_id", "tax_id": "tax_ids", "taxes_id": "tax_ids",
           "contract_id": "version_id", "groups_id": "group_ids", "ref": "memo",
           "date_start": "contract_date_start", "date_end": "contract_date_end"}
SKIP_TYPES = {"binary", "properties", "properties_definition", "reference", "many2one_reference", "one2many"}


def norm_rel(r):
    r = B.canon(r)
    return REL_ALIAS.get(r, r)


# ------------------------------------------------------------------ logging helpers
_dropped_n = {}


def _csv(path, header, row):
    with _lock:
        new = not os.path.exists(path)
        with open(path, "a", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            if new:
                w.writerow(header)
            w.writerow(row)


def failed(model, sid, name, why):
    log.error("[%s] src id %s (%s) FAIL: %s", model, sid, name, str(why)[:300])
    _csv(FAILED_CSV, ["model", "src_id", "name", "reason"], [model, sid, name, str(why)[:500]])


def dropped(model, sid, field, val, why):
    key = (model, field, why)
    with _lock:
        _dropped_n[key] = _dropped_n.get(key, 0) + 1
        n = _dropped_n[key]
    if n <= 30:      # har (model, field, wajah) ki sirf pehli 30 rows file mein, baqi sirf ginti
        _csv(DROPPED_CSV, ["model", "src_id", "field", "src_value", "reason"], [model, sid, field, str(val)[:80], why])


def mismatch(model, sid, name, what):
    log.warning("[%s] src %s (%s) MISMATCH: %s", model, sid, name, what)
    _csv(MISMATCH_CSV, ["model", "src_id", "name", "detail"], [model, sid, name, what])


# ------------------------------------------------------------------ id map (master + tx alag files)
class TxIdMap(B.IdMap):
    def __init__(self, path, tx_path):
        super().__init__(path)
        self.tx_path = tx_path
        self.t = json.load(open(tx_path)) if os.path.exists(tx_path) else {}
        self.lk = threading.Lock()

    def _store(self, model):
        return self.t if model in TX_MODELS else self.d

    def get(self, model, sid):
        return self._store(model).get(model, {}).get(str(sid))

    def set(self, model, sid, did):
        with self.lk:
            self._store(model).setdefault(model, {})[str(sid)] = did

    def save(self):
        with self.lk:
            for p, d in ((self.path, self.d), (self.tx_path, self.t)):
                tmp = p + ".tmp"
                with open(tmp, "w") as fh:
                    json.dump(d, fh)
                os.replace(tmp, p)


def rid(smodel, sid):
    """src id -> dst id (idmap ya xmlid)"""
    return B.resolve(smodel, sid)


# ------------------------------------------------------------------ dst call helpers
RETRY_WORDS = ("could not serialize", "concurrent update", "deadlock", "lock timeout", "could not obtain lock",
               "http 502", "http 503", "http 504")


def dcall(model, method, *a, **kw):
    """dst call + serialization/gateway errors par retry (rollback hua hota hai, isliye safe)"""
    for i in range(5):
        try:
            return B.dst.call(model, method, *a, **kw)
        except RuntimeError as e:
            if i < 4 and any(w in str(e).lower() for w in RETRY_WORDS):
                time.sleep(1.0 * (i + 1) + random.random())
                continue
            raise


def raw_json2(model, method, body):
    """poora result wapas (Odoo2.call create par sirf pehli id deta hai)"""
    cfg = B.DST
    req = urllib.request.Request(
        "%s/json/2/%s/%s" % (cfg["url"], model, method), data=json.dumps(body).encode(), method="POST",
        headers={"Authorization": "bearer " + cfg["api_key"], "X-Odoo-Database": cfg["db"],
                 "Content-Type": "application/json", "User-Agent": "akg-migration-tx"})
    try:
        return json.loads(urllib.request.urlopen(req, timeout=600).read().decode())
    except urllib.error.HTTPError as e:
        txt = e.read().decode(errors="replace")
        try:
            txt = json.loads(txt).get("message", txt)
        except Exception:
            pass
        raise RuntimeError("HTTP %s %s/%s: %s" % (e.code, model, method, str(txt)[:300]))


def bulk_create(dm, vals_list, ctx=None):
    for i in range(4):
        try:
            return raw_json2(dm, "create", {"vals_list": vals_list, "context": ctx or B.CTX})
        except RuntimeError as e:
            if i < 3 and any(w in str(e).lower() for w in RETRY_WORDS):
                time.sleep(1.0 * (i + 1) + random.random())
                continue
            raise


def dst_has(model, field):
    f = B.dst.fields(model)
    return bool(f) and field in f


def dst_selection(model, field):
    f = (B.dst.fields(model) or {}).get(field) or {}
    return [x[0] for x in (f.get("selection") or [])]


# ------------------------------------------------------------------ field mapper
class Mapper:
    """src record -> dst vals (naam + type match, many2one/many2many id-map se)"""

    def __init__(self, sm, dm, skip=(), rename=None):
        self.sm, self.dm = sm, dm
        self.sf, self.df = B.src.fields(sm), B.dst.fields(dm)
        ms, md = B.model_meta(B.src, sm), B.model_meta(B.dst, dm)
        ren, skip = rename or {}, set(skip)
        self.plan = {}
        for f, s in self.sf.items():
            if f in B.GLOBAL_SKIP or f in skip or s["type"] in SKIP_TYPES:
                continue
            if f.startswith(("message_", "activity_", "website_message")):
                continue
            if not (s.get("store") or s.get("company_dependent")):
                continue
            n = ren.get(f) or (f if f in self.df else RENAMES.get(f))
            if not n or n not in self.df:
                continue
            d = self.df[n]
            if d["type"] != s["type"]:
                continue
            if s["type"] in ("many2one", "many2many") and norm_rel(s["relation"]) != B.canon(d["relation"]):
                continue
            if d.get("readonly") and (n in md["computed"] or n in md["related"]):
                continue
            self.plan[f] = n

    @property
    def read_fields(self):
        return sorted(self.plan)

    def map(self, rec, must=()):
        """-> vals, later[(n,'many2one',rel,sid)], missing[(field, rel, sid)]"""
        vals, later, missing = {}, [], []
        sid_rec = rec.get("id")
        for f, n in self.plan.items():
            v = rec.get(f)
            s, d = self.sf[f], self.df[n]
            t = s["type"]
            if t == "many2one":
                if not v:
                    continue
                sid = v[0] if isinstance(v, (list, tuple)) else v
                r_ = rid(s["relation"], sid)
                if r_:
                    vals[n] = r_
                elif f in must or n in must:
                    missing.append((f, s["relation"], sid))
                else:
                    later.append((n, "many2one", s["relation"], sid))
            elif t == "many2many":
                if not v:
                    continue
                ids = []
                for i in v:
                    r_ = rid(s["relation"], i)
                    if r_:
                        ids.append(r_)
                    else:
                        dropped(self.sm, sid_rec, f, i, "m2m %s nahi mila" % s["relation"])
                if ids:
                    vals[n] = [[6, 0, ids]]
            elif t == "selection":
                if not v:
                    continue
                allowed = [x[0] for x in (d.get("selection") or [])]
                if not allowed or v in allowed:
                    vals[n] = v
                else:
                    dropped(self.sm, sid_rec, f, v, "selection value dst mein nahi")
            else:
                if v is False and t != "boolean":
                    continue
                vals[n] = v
        return vals, later, missing


def remap_analytic(vals):
    """analytic_distribution ki keys (src analytic account ids) -> dst ids"""
    ad = vals.get("analytic_distribution")
    if not ad or not isinstance(ad, dict):
        vals.pop("analytic_distribution", None)
        return vals
    new = {}
    for k, pct in ad.items():
        ids = [rid("account.analytic.account", int(x)) for x in str(k).split(",") if x.strip()]
        if ids and all(ids):
            new[",".join(str(i) for i in ids)] = pct
        else:
            dropped("analytic_distribution", None, "key", k, "analytic account map nahi")
    if new:
        vals["analytic_distribution"] = new
    else:
        vals.pop("analytic_distribution")
    return vals


# ------------------------------------------------------------------ options
class Opt:
    dry = False
    limit = 0
    workers = 1
    date_from = None
    date_to = None
    post_method = "state"     # "state" (seedha state=posted, COGS/sequence dobara nahi banta) ya "action" (action_post)
    company = None


O = Opt()
CTX_DOC = dict(B.CTX, check_move_validity=False, skip_invoice_sync=True, skip_invoice_line_sync=True,
               skip_account_move_synchronization=True, mail_activity_quick_update=True, no_new_invoice=True)
_state = {"defer": set(), "pay_moves": set(), "st_moves": set()}


# ------------------------------------------------------------------ auto-map of masters (jo idmap mein nahi)
def _plain(keys, extra_read=()):
    return dict(keys=keys, extra=list(extra_read))


_pml_cache = {}


def find_pml(rec):
    """account.payment.method.line: journal + payment method (code/type)"""
    j = rid("account.journal", rec["journal_id"][0]) if rec.get("journal_id") else None
    if not j or not rec.get("payment_method_id"):
        return None
    pm = rec["payment_method_id"][0]
    if pm not in _pml_cache:
        r = B.src.call("account.payment.method", "read", [pm], fields=["code", "payment_type"])[0]
        _pml_cache[pm] = (r["code"], r["payment_type"])
    code, ptype = _pml_cache[pm]
    res = B.dst.call("account.payment.method.line", "search",
                     [("journal_id", "=", j), ("payment_method_id.code", "=", code),
                      ("payment_method_id.payment_type", "=", ptype)], limit=1, context=B.CTX)
    return res[0] if res else None


def find_product(rec):
    try:
        v = B.find_variant(rec)
        if v:
            return v
    except Exception:
        pass
    for k in ("default_code", "barcode"):
        if rec.get(k):
            r = B.dst.call("product.product", "search", [(k, "=", rec[k])], limit=1, context=B.CTX)
            if r:
                return r[0]
    return None


def find_version_lookup(rec):
    e = rid("hr.employee", rec["employee_id"][0]) if rec.get("employee_id") else None
    if not e:
        return None
    dom = [("employee_id", "=", e)]
    if rec.get("date_start"):
        dom.append(("contract_date_start", "=", rec["date_start"]))
    r = B.dst.call("hr.version", "search", dom, limit=1, order="id desc", context=B.CTX)
    return r[0] if r else None


def find_input_type(rec):
    if not rec.get("code"):
        return None
    r = B.dst.call("hr.salary.rule", "search", [("code", "=", rec["code"])], limit=1, context=B.CTX)
    return r[0] if r else None


def find_leave_type(rec):
    r = B.dst.call("hr.work.entry.type", "search", [("name", "=", rec["name"])], limit=1, context=B.CTX)
    return r[0] if r else None


AUTOMAP = {
    "account.account": dict(keys=["code"]),
    "account.journal": dict(keys=["code", "company_id"]),
    "account.tax": dict(keys=["name", "type_tax_use", "company_id"]),
    "account.payment.term": dict(keys=["name"]),
    "account.fiscal.position": dict(keys=["name", "company_id"]),
    "account.analytic.account": dict(keys=["name"]),
    "account.payment.method.line": dict(finder=find_pml, extra=["journal_id", "payment_method_id"]),
    "res.users": dict(keys=["login"]),
    "res.partner.bank": dict(keys=["acc_number"]),
    "hr.employee": dict(keys=["name", "company_id"]),
    "hr.department": dict(keys=["name", "company_id"]),
    "hr.contract": dict(finder=find_version_lookup, dst="hr.version", extra=["employee_id", "date_start"]),
    "hr.payroll.structure": dict(keys=["name"]),
    "hr.salary.rule": dict(finder=B.find_salary_rule, extra=["struct_id", "code"]),
    "hr.work.entry.type": dict(keys=["code"]),
    "hr.leave.type": dict(finder=find_leave_type, dst="hr.work.entry.type", extra=["name"]),
    "hr.payslip.input.type": dict(finder=find_input_type, dst="hr.salary.rule", extra=["code"]),
    "product.product": dict(finder=find_product,
                            extra=["product_tmpl_id", "product_template_variant_value_ids", "default_code", "barcode"]),
    "uom.uom": dict(keys=["name"]),
    "product.pricelist": dict(keys=["name"]),
    "stock.warehouse": dict(keys=["code", "company_id"]),
    "stock.location": dict(keys=["complete_name", "company_id"]),
    "stock.picking.type": dict(keys=["name", "warehouse_id"]),
    "crm.team": dict(keys=["name"]),
    "crm.stage": dict(keys=["name"]),
    "crm.lost.reason": dict(keys=["name"]),
    "crm.tag": dict(keys=["name"]),
    "utm.source": dict(keys=["name"]),
    "utm.medium": dict(keys=["name"]),
    "utm.campaign": dict(keys=["name"]),
    "pos.config": dict(keys=["name"]),
    "pos.payment.method": dict(keys=["name"]),
    "res.currency": dict(keys=["name"]),
    "approval.category": dict(keys=["name"]),
}
# check ke liye zaroori masters (src model, ye kaun se step ke liye chahiye)
NEED = ["res.partner", "product.product", "account.account", "account.journal", "account.tax",
        "account.payment.term", "res.users", "hr.employee", "hr.contract", "hr.salary.rule",
        "hr.work.entry.type", "hr.leave.type", "crm.team", "crm.stage", "uom.uom", "product.pricelist",
        "account.payment.method.line", "res.partner.bank", "account.analytic.account", "pos.config",
        "pos.payment.method", "stock.location"]


def automap(sm, cfg, apply=True):
    """src records jo idmap mein nahi, unko dst mein natural key se dhoondh kar idmap mein daalta hai.
    -> (total, already, newly, missing_list[(id,label)])"""
    if B.src.fields(sm) is None:
        return None
    dm = cfg.get("dst") or B.dst_model(sm)
    if not dm or B.dst.fields(dm) is None:
        return "dst-model-nahi"
    sf, df = B.src.fields(sm), B.dst.fields(dm)
    keys = [k for k in cfg.get("keys", []) if k in sf and B.dname(k, df, {}) in df]
    ids = B.src.call(sm, "search", [], order="id", context=B.CTX)
    todo = [i for i in ids if not rid(sm, i)]
    already, newly, missing = len(ids) - len(todo), 0, []
    rf = sorted((set(keys) | set(cfg.get("extra", [])) | {"name", "code", "login"}) & set(sf))
    for i in range(0, len(todo), 200):
        for rec in B.src.call(sm, "read", todo[i:i + 200], fields=rf, context=B.CTX):
            did = None
            try:
                if cfg.get("finder"):
                    did = cfg["finder"](rec)
                elif keys:
                    dom = []
                    for k in keys:
                        v = rec.get(k)
                        if isinstance(v, list):
                            v = rid(sf[k]["relation"], v[0])
                            if not v:
                                dom = None
                                break
                        if v is False:
                            continue
                        dom.append((B.dname(k, df, {}), "=", v))
                    if dom:
                        r = B.dst.call(dm, "search", dom, limit=1, context=B.CTX)
                        did = r[0] if r else None
            except Exception as e:
                log.debug("automap %s %s: %s", sm, rec.get("id"), e)
            if did:
                if apply:
                    B.idmap.set(sm, rec["id"], did)
                newly += 1
            else:
                missing.append((rec["id"], rec.get("name") or rec.get("code") or rec.get("login") or ""))
    return len(ids), already, newly, missing


def ensure_masters(models=None, quiet=False):
    out = {}
    for sm in (models or list(AUTOMAP)):
        cfg = AUTOMAP.get(sm)
        if not cfg:
            continue
        r = automap(sm, cfg)
        out[sm] = r
        if isinstance(r, tuple) and not quiet and (r[2] or r[3]):
            log.info("automap %-30s total=%d pehle-se=%d naya-mila=%d nahi-mila=%d", sm, r[0], r[1], r[2], len(r[3]))
    B.idmap.save()
    return out


def build_repartition_map():
    """account.tax.repartition.line: (tax, invoice/refund, type) ke andar position se src -> dst"""
    sm, dm = "account.tax.repartition.line", "account.tax.repartition.line"
    sf, df = B.src.fields(sm), B.dst.fields(dm)
    if not sf or not df:
        log.warning("repartition line model nahi mila - tax lines ka repartition link nahi banega")
        return 0
    new_style = "tax_id" in df and "document_type" in df
    srecs = B.src.call(sm, "search_read", [], fields=["invoice_tax_id", "refund_tax_id", "repartition_type", "sequence"]
                       if "invoice_tax_id" in sf else ["tax_id", "document_type", "repartition_type", "sequence"],
                       order="sequence, id", context=B.CTX)
    drecs = B.dst.call(dm, "search_read", [], fields=(["tax_id", "document_type", "repartition_type", "sequence"]
                                                      if new_style else ["invoice_tax_id", "refund_tax_id",
                                                                         "repartition_type", "sequence"]),
                       order="sequence, id", context=B.CTX)

    def key(r, src_side):
        if "tax_id" in r and r.get("tax_id") is not None and "document_type" in r:
            tax, doc = r["tax_id"], r["document_type"]
        else:
            tax, doc = (r["invoice_tax_id"], "invoice") if r.get("invoice_tax_id") else (r.get("refund_tax_id"), "refund")
        if not tax:
            return None
        t = tax[0] if isinstance(tax, (list, tuple)) else tax
        t = rid("account.tax", t) if src_side else t
        return (t, doc, r["repartition_type"]) if t else None

    dgroups = {}
    for r in drecs:
        k = key(r, False)
        if k:
            dgroups.setdefault(k, []).append(r["id"])
    sgroups = {}
    for r in srecs:
        k = key(r, True)
        if k:
            sgroups.setdefault(k, []).append(r["id"])
    n = 0
    for k, sl in sgroups.items():
        dl = dgroups.get(k, [])
        for s_id, d_id in zip(sl, dl):
            B.idmap.set(sm, s_id, d_id)
            n += 1
        if len(sl) != len(dl):
            log.warning("repartition %s: src %d vs dst %d lines", k, len(sl), len(dl))
    log.info("repartition lines mapped: %d", n)
    B.idmap.save()
    return n


# ------------------------------------------------------------------ generic header+lines engine
def _date_domain(spec):
    dom = list(spec.get("domain", []))
    f = spec.get("date_field")
    if f and O.date_from:
        dom.append((f, ">=", O.date_from))
    if f and O.date_to:
        dom.append((f, "<=", O.date_to))
    return dom


def _model_ok(sm, dm=None):
    if B.src.fields(sm) is None:
        log.warning("[%s] Odoo16 mein model nahi - skip", sm)
        return None
    dm = dm or B.dst_model(sm)
    if not dm or B.dst.fields(dm) is None:
        log.warning("[%s] Odoo20 mein model NAHI (%s) - skip, manual handling chahiye", sm, dm or sm)
        return None
    return dm


class Counter:
    def __init__(self):
        self.ok = self.err = self.skip = 0
        self.lk = threading.Lock()

    def add(self, what):
        with self.lk:
            setattr(self, what, getattr(self, what) + 1)


def run_doc(spec):
    sm = spec["model"]
    dm = _model_ok(sm, spec.get("dst_model"))
    if not dm:
        return
    hm = Mapper(sm, dm, spec.get("skip", ()), spec.get("rename"))
    lines = []
    for ls in spec.get("lines", []):
        ldm = _model_ok(ls["model"], ls.get("dst_model"))
        if ldm and ls["inline"] in B.dst.fields(dm):
            lines.append((ls, Mapper(ls["model"], ldm, ls.get("skip", ()), ls.get("rename")), ldm))
        elif ldm:
            log.warning("[%s] dst %s mein '%s' field nahi - lines skip", sm, dm, ls["inline"])
    if spec.get("pre"):
        spec["pre"]()
    ids = B.src.call(sm, "search", _date_domain(spec), order="id", context=B.CTX)
    total = len(ids)
    ids = [i for i in ids if not B.idmap.get(sm, i)]
    if O.limit:
        ids = ids[:O.limit]
    workers = 1 if spec.get("serial") else max(1, O.workers)
    log.info("== %s -> %s : %d total, %d baqi (workers=%d, dry=%s)", sm, dm, total, len(ids), workers, O.dry)
    cnt = Counter()
    ctx = dict(B.CTX, **spec.get("ctx", {}))
    hfields = sorted(set(hm.read_fields) | set(spec.get("extra_read", [])) | {"id"})
    bs = spec.get("batch", 40)

    def process(rec, lines_data):
        sid = rec["id"]
        name = rec.get("name") or rec.get("display_name") or ""
        try:
            vals, later, missing = hm.map(rec, spec.get("must", ()))
            if missing:
                raise ValueError("zaroori link map nahi: %s (pehle uska master/doc migrate karo)" % missing)
            if spec.get("hook"):
                vals = spec["hook"](vals, rec)
                if vals is None:
                    cnt.add("skip")
                    return
            pairs = []     # (ls, ldm, kept_src_line_ids, verify_sums)
            for (ls, lm, ldm) in lines:
                lrecs = lines_data[ls["model"]].get(sid, [])
                cmds, kept = [], []
                sums = {}
                for lr in lrecs:
                    lv, ll, lmiss = lm.map(lr, ls.get("must", ()))
                    if lmiss:
                        raise ValueError("line %s: zaroori link map nahi: %s" % (lr["id"], lmiss))
                    for x in ll:
                        dropped(ls["model"], lr["id"], x[0], x[3], "%s map nahi (line mein link hata diya)" % x[2])
                    lv = remap_analytic(lv)
                    if ls.get("hook"):
                        lv = ls["hook"](lv, lr, rec)
                        if lv is None:
                            continue
                    for vf in ls.get("verify", []):
                        sums[vf] = sums.get(vf, 0.0) + float(lv.get(vf) or 0.0)
                    cmds.append([0, 0, lv])
                    kept.append(lr["id"])
                vals[ls["inline"]] = cmds
                pairs.append((ls, ldm, kept, sums))
            if O.dry:
                cnt.add("ok")
                return
            did = spec["finder"](rec) if spec.get("finder") else None
            adopted = bool(did)
            if not did:
                did = dcall(dm, "create", vals, context=ctx)
            B.idmap.set(sm, sid, did)
            for n, t, rel, sv in later:
                B.PENDING.append((dm, did, n, t, rel, sv))
            for (ls, ldm, kept, sums) in pairs:
                _map_lines(spec, ls, dm, ldm, did, kept, sums, sid, name)
            if spec.get("after"):
                spec["after"](rec, did, ctx)
            cnt.add("ok")
        except Exception as e:
            cnt.add("err")
            failed(sm, sid, name, e)

    t0 = time.time()
    for i in range(0, len(ids), bs):
        chunk = ids[i:i + bs]
        recs = B.src.call(sm, "read", chunk, fields=hfields, context=B.CTX)
        ldata = {}
        for (ls, lm, ldm) in lines:
            lf = sorted(set(lm.read_fields) | set(ls.get("extra_read", [])) | {"id", ls["parent"]})
            rows = B.src.call(ls["model"], "search_read", [(ls["parent"], "in", chunk)], fields=lf,
                              order="id", context=B.CTX)
            g = {}
            for r in rows:
                g.setdefault(r[ls["parent"]][0], []).append(r)
            ldata[ls["model"]] = g
        if workers > 1:
            with ThreadPoolExecutor(workers) as ex:
                list(ex.map(lambda r: process(r, ldata), recs))
        else:
            for r in recs:
                process(r, ldata)
        if (i // bs) % 5 == 0:
            B.idmap.save()
            el = time.time() - t0
            log.info("[%s] %d/%d  ok=%d err=%d  (%.1f rec/s)", sm, i + len(chunk), len(ids), cnt.ok, cnt.err,
                     (i + len(chunk)) / el if el else 0)
    if not O.dry:
        B.flush_pending()
        if B.PENDING:
            log.warning("[%s] %d optional links abhi bhi unresolved (tx_dropped.csv mein nahi, baad mein dobara run karo)",
                        sm, len(B.PENDING))
            B.PENDING.clear()
    B.idmap.save()
    if spec.get("end"):
        spec["end"]()
    log.info("== %s done: ok=%d err=%d skip=%d", sm, cnt.ok, cnt.err, cnt.skip)


def _map_lines(spec, ls, dm, ldm, did, kept_src_ids, sums, sid, name):
    """dst ke bane hue lines ko src line ids se match karke idmap mein daalta hai (+ optional sum check)"""
    try:
        pf = ls.get("inline_parent")
        if pf not in (B.dst.fields(ldm) or {}):
            pf = _parent_field(dm, ldm)
        vf = ["id"] + [f for f in ls.get("verify", []) if f in B.dst.fields(ldm)]
        rows = dcall(ldm, "search_read", [(pf, "=", did)], fields=vf, order="id", context=B.CTX)
    except Exception as e:
        mismatch(spec["model"], sid, name, "dst lines parh nahi sake (%s) - links is document ke liye skip" % str(e)[:150])
        return False
    if len(rows) != len(kept_src_ids):
        mismatch(spec["model"], sid, name, "lines: src %d vs dst %d (dynamic sync ne lines badal di?) - reconcile/links is "
                                           "document ke liye skip honge" % (len(kept_src_ids), len(rows)))
        return False
    for sl, r in zip(sorted(kept_src_ids), rows):
        B.idmap.set(ls["model"], sl, r["id"])
    for f, v in sums.items():
        dv = sum(r.get(f) or 0.0 for r in rows)
        if abs(dv - v) > 0.05:
            mismatch(spec["model"], sid, name, "%s: src %.2f vs dst %.2f" % (f, v, dv))
    return True


def _parent_field(dm, ldm):
    """dst line model mein parent many2one ka naam (relation se)"""
    cands = [f for f, d in (B.dst.fields(ldm) or {}).items()
             if d["type"] == "many2one" and d.get("relation") == dm]
    req = [f for f in cands if B.dst.fields(ldm)[f].get("required")]
    if req:
        return req[0]
    if cands:
        return cands[0]
    raise RuntimeError("%s mein %s ka parent field nahi mila" % (ldm, dm))


def write_state(dm, did, rec, field="state", fallback=None, extra=None):
    """document ko seedha uski purani state par le jao (workflow dobara chalaye baghair)"""
    st = rec.get(field)
    if not st:
        return
    allowed = dst_selection(dm, field)
    vals = dict(extra or {})
    if allowed and st not in allowed:
        if st == "done" and "locked" in (B.dst.fields(dm) or {}):
            st, vals["locked"] = "sale" if "sale" in allowed else allowed[0], True
        elif fallback and fallback.get(st) in allowed:
            st = fallback[st]
        else:
            dropped(dm, rec.get("id"), field, st, "state dst mein nahi")
            return
    if st == "draft" and not vals:
        return
    vals[field] = st
    try:
        dcall(dm, "write", [did], vals, context=B.CTX)
    except Exception as e:
        failed(dm, rec.get("id"), "state->%s" % st, e)


# ------------------------------------------------------------------ STEP: masters (halke masters)
def h_analytic(vals, rec, later):
    if dst_has("account.analytic.account", "plan_id") and not vals.get("plan_id"):
        if "plan" not in _state:
            p = B.dst.call("account.analytic.plan", "search", [], limit=1, order="id", context=B.CTX)
            _state["plan"] = p[0] if p else B.dst.call("account.analytic.plan", "create", {"name": "Default"}, context=B.CTX)
        vals["plan_id"] = _state["plan"]
    return vals


MASTER_STEPS = [
    dict(model="utm.source", key=["name"]), dict(model="utm.medium", key=["name"]),
    dict(model="utm.campaign", key=["name"]),
    dict(model="crm.stage", key=["name"]), dict(model="crm.lost.reason", key=["name"]),
    dict(model="crm.tag", key=["name"]),
    dict(model="account.analytic.account", key=["name", "company_id"], hook=h_analytic),
    dict(model="sale.temporal.recurrence", key=["duration", "unit"]),
    dict(model="product.pricing"),                      # rental prices
    dict(model="approval.category", key=["name"]),
    dict(model="approval.category.approver"),
]


def step_masters():
    ensure_masters(quiet=True)
    for st in MASTER_STEPS:
        B.DRY = O.dry
        B.run_step(st)
    B.flush_pending()
    B.idmap.save()


# ------------------------------------------------------------------ STEP: crm
def _find_by(dm, dom, **kw):
    if any(v is False for (_, _, v) in dom):
        return None
    r = B.dst.call(dm, "search", dom, limit=1, context=B.CTX)
    return r[0] if r else None


# ------------------------------------------------------------------ STEP: sales (+ rental)
def after_order(dm, extra_status=True):
    def _after(rec, did, ctx):
        write_state(dm, did, rec, fallback={"done": "sale"})
        if extra_status and rec.get("invoice_status"):
            try:
                dcall(dm, "write", [did], {"invoice_status": rec["invoice_status"]}, context=B.CTX)
            except Exception:
                pass
    return _after


SPEC_SALE = dict(
    model="sale.order", date_field="date_order", must=["partner_id"], extra_read=["state", "invoice_status", "name"],
    skip=["state", "invoice_status", "amount_untaxed", "amount_tax", "amount_total", "tax_totals", "access_token",
          "signature", "signed_by", "signed_on", "analytic_account_id", "locked"],
    finder=lambda rec: _find_by("sale.order", [("name", "=", rec.get("name")), ("partner_id", "=", rid("res.partner", rec["partner_id"][0]) if rec.get("partner_id") else False)]),
    after=after_order("sale.order"),
    lines=[dict(model="sale.order.line", parent="order_id", inline="order_line", verify=[],
                skip=["state", "invoice_lines", "qty_invoiced", "qty_to_invoice", "invoice_status", "order_id",
                      "purchase_line_ids", "move_ids", "reserved_lot_ids", "pickedup_lot_ids", "returned_lot_ids"],
                extra_read=["display_type"])])


# ------------------------------------------------------------------ STEP: purchase
SPEC_PREQ = dict(
    model="purchase.request", date_field="date_start", extra_read=["state", "name"],
    skip=["state", "line_ids"],
    finder=lambda rec: _find_by("purchase.request", [("name", "=", rec.get("name"))]),
    after=lambda rec, did, ctx: write_state("purchase.request", did, rec),
    lines=[dict(model="purchase.request.line", parent="request_id", inline="line_ids",
                skip=["purchase_lines", "purchase_state", "request_id", "purchased_qty", "qty_in_progress", "qty_done"])])

SPEC_PO = dict(
    model="purchase.order", date_field="date_order", must=["partner_id"], extra_read=["state", "invoice_status", "name"],
    skip=["state", "invoice_status", "amount_untaxed", "amount_tax", "amount_total", "tax_totals", "access_token",
          "receipt_status", "locked"],
    finder=lambda rec: _find_by("purchase.order", [("name", "=", rec.get("name")), ("partner_id", "=", rid("res.partner", rec["partner_id"][0]) if rec.get("partner_id") else False)]),
    after=after_order("purchase.order"),
    lines=[dict(model="purchase.order.line", parent="order_id", inline="order_line",
                skip=["state", "invoice_lines", "qty_invoiced", "qty_to_invoice", "order_id", "move_ids",
                      "purchase_request_lines", "sale_line_id", "sale_order_id"],
                extra_read=["display_type"])])

SPEC_PREQ_ALLOC = dict(model="purchase.request.allocation", dst_model=None)


# ------------------------------------------------------------------ STEP: accounting
MOVE_SKIP = ["state", "payment_state", "status_in_payment", "amount_untaxed", "amount_tax", "amount_total",
             "amount_residual", "amount_untaxed_signed", "amount_tax_signed", "amount_total_signed",
             "amount_residual_signed", "payment_id", "statement_line_id", "stock_move_id", "reversed_entry_id",
             "posted_before", "inalterable_hash", "secure_sequence_number", "asset_id", "asset_remaining_value",
             "asset_depreciated_value", "asset_manually_modified", "tax_closing_end_date", "tax_closing_report_id",
             "statement_id", "access_token", "edi_state", "extract_state", "tax_totals", "hash",
             "line_ids", "invoice_line_ids"]
LINE_SKIP = ["move_id", "parent_state", "reconciled", "matching_number", "full_reconcile_id", "amount_residual",
             "amount_residual_currency", "payment_id", "statement_line_id", "statement_id", "tax_tag_ids",
             "reconcile_model_id", "asset_ids", "asset_id", "move_name", "move_type", "tax_audit",
             "expected_pay_date", "tax_group_id"]


def h_move(vals, rec):
    if rec.get("name") in (False, None, ""):
        vals.pop("name", None)
    return vals


def h_move_line(lv, lr, mv):
    """debit/credit/balance/amount_currency har haal mein jayein (dst mein 'computed' hon tab bhi) - total check baad mein hota hai"""
    df = B.dst.fields("account.move.line")
    for k in ("debit", "credit", "balance", "amount_currency"):
        if k in df and k in lr and lr[k] is not False and k not in lv:
            lv[k] = lr[k]
    return lv


def _post_move(did, rec, ctx):
    try:
        if O.post_method == "action":
            dcall("account.move", "action_post", ids=[did], context=ctx)
        else:
            vals = {"state": "posted"}
            if dst_has("account.move", "posted_before"):
                vals["posted_before"] = True
            dcall("account.move", "write", [did], vals, context=ctx)
    except Exception as e:
        if O.post_method == "state":      # fallback: normal post
            dcall("account.move", "action_post", ids=[did], context=ctx)
        else:
            raise e


def after_move(rec, did, ctx):
    st = rec.get("state")
    if st == "posted":
        if rec["id"] in _state["defer"]:
            return                          # payment/bank-statement record link hone ke baad post hoga
        _post_move(did, rec, ctx)
    elif st == "cancel":
        write_state("account.move", did, rec)


def find_move(rec):
    if not rec.get("name") or rec["name"] == "/":
        return None
    j = rid("account.journal", rec["journal_id"][0]) if rec.get("journal_id") else None
    if not j:
        return None
    return _find_by("account.move", [("name", "=", rec["name"]), ("journal_id", "=", j), ("date", "=", rec["date"])])


def pre_moves():
    build_repartition_map()
    pm = B.src.call("account.payment", "search_read", [], fields=["move_id"], context=B.CTX) if B.src.fields("account.payment") else []
    sl = B.src.call("account.bank.statement.line", "search_read", [], fields=["move_id"], context=B.CTX) \
        if B.src.fields("account.bank.statement.line") else []
    _state["pay_moves"] = {r["move_id"][0] for r in pm if r.get("move_id")}
    _state["st_moves"] = {r["move_id"][0] for r in sl if r.get("move_id")}
    _state["defer"] = _state["pay_moves"] | _state["st_moves"]
    log.info("deferred-post moves: payments=%d bank-lines=%d", len(_state["pay_moves"]), len(_state["st_moves"]))


def end_moves():
    """reversal links"""
    rows = B.src.call("account.move", "search_read", [("reversed_entry_id", "!=", False)],
                      fields=["reversed_entry_id"], context=B.CTX)
    n = 0
    for r in rows:
        if not r.get("reversed_entry_id"):
            continue
        a, b = B.idmap.get("account.move", r["id"]), B.idmap.get("account.move", r["reversed_entry_id"][0])
        if a and b and not O.dry:
            try:
                dcall("account.move", "write", [a], {"reversed_entry_id": b}, context=CTX_DOC)
                n += 1
            except Exception as e:
                dropped("account.move", r["id"], "reversed_entry_id", b, str(e)[:60])
    log.info("reversal links: %d", n)


SPEC_MOVE = dict(
    model="account.move", date_field="date", must=["journal_id"], ctx=CTX_DOC, batch=25,
    extra_read=["state", "name", "journal_id", "date"], skip=MOVE_SKIP, hook=h_move, finder=find_move,
    after=after_move, pre=pre_moves, end=end_moves,
    lines=[dict(model="account.move.line", parent="move_id", inline="line_ids", must=["account_id"],
                verify=["debit", "credit"], skip=LINE_SKIP, hook=h_move_line,
                extra_read=["display_type", "debit", "credit", "balance", "amount_currency"],
                inline_parent="move_id")])


def link_and_post_move(smove_id, dmove, ctx):
    """payment / bank-statement record bane ke baad uska (draft) move post karo"""
    if not dmove:
        return
    src_state = B.src.call("account.move", "read", [smove_id], fields=["state"], context=B.CTX)[0]["state"]
    if src_state == "posted":
        _post_move(dmove, {"id": smove_id}, ctx)
    elif src_state == "cancel":
        dcall("account.move", "write", [dmove], {"state": "cancel"}, context=ctx)


def _link_record(dm, sm, rec, vals, dmove, ctx):
    """record ko EXISTING move se jodo (line_ids ko [6,0,existing] se bachao taake Odoo default lines na daale)"""
    before = dcall("account.move", "read", [dmove], fields=["line_ids"], context=B.CTX)[0]["line_ids"]
    base = dict(vals, move_id=dmove)
    variants = []
    if dst_has(dm, "line_ids"):
        variants.append(dict(base, line_ids=[[6, 0, before]]))
    variants.append(base)
    did, last = None, None
    for v in variants:
        try:
            did = dcall(dm, "create", v, context=ctx)
            break
        except Exception as e:
            last = e
    if not did:
        raise last
    after = dcall("account.move", "read", [dmove], fields=["line_ids"], context=B.CTX)[0]["line_ids"]
    extra = [x for x in after if x not in before]
    if extra:
        dcall("account.move.line", "unlink", ids=extra, context=ctx)
        log.warning("[%s] src %s: Odoo ne %d extra default lines bana di thin - hata di", sm, rec["id"], len(extra))
    return did


def run_linked(sm, dm, skip, rename, must, move_field="move_id", extra_vals=None, finder=None, pre=None):
    """account.payment / account.bank.statement.line: pehle se migrate hue move ke saath record banao"""
    if not _model_ok(sm, dm):
        return
    mapper = Mapper(sm, dm, skip, rename)
    if pre:
        pre()
    ids = B.src.call(sm, "search", [], order="id", context=B.CTX)
    ids = [i for i in ids if not B.idmap.get(sm, i)]
    if O.limit:
        ids = ids[:O.limit]
    log.info("== %s -> %s : %d baqi", sm, dm, len(ids))
    cnt = Counter()
    ctx = dict(CTX_DOC)

    def one(rec):
        try:
            smove = rec[move_field][0] if rec.get(move_field) else None
            dmove = rid("account.move", smove) if smove else None
            if not dmove:
                cnt.add("skip")
                return
            vals, later, missing = mapper.map(rec, must)
            if missing:
                raise ValueError("zaroori link map nahi: %s" % missing)
            if extra_vals:
                vals = extra_vals(vals, rec)
                if vals is None:
                    cnt.add("skip")
                    return
            if O.dry:
                cnt.add("ok")
                return
            did = None
            try:
                did = _link_record(dm, sm, rec, vals, dmove, ctx)
                B.idmap.set(sm, rec["id"], did)
            except Exception as e:
                failed(sm, rec["id"], rec.get("name") or "", "record link nahi hua (move phir bhi post hoga): %s" % e)
                cnt.add("err")
            link_and_post_move(smove, dmove, ctx)
            if did:
                cnt.add("ok")
        except Exception as e:
            cnt.add("err")
            failed(sm, rec.get("id"), rec.get("name") or "", e)

    rf = sorted(set(mapper.read_fields) | {"id", move_field, "state"} & set(B.src.fields(sm)))
    bs = 50
    t0 = time.time()
    for i in range(0, len(ids), bs):
        recs = B.src.call(sm, "read", ids[i:i + bs], fields=rf, context=B.CTX)
        if max(1, O.workers) > 1:
            with ThreadPoolExecutor(O.workers) as ex:
                list(ex.map(one, recs))
        else:
            for r in recs:
                one(r)
        if (i // bs) % 10 == 0:
            B.idmap.save()
            log.info("[%s] %d/%d ok=%d err=%d skip=%d", sm, i + len(recs), len(ids), cnt.ok, cnt.err, cnt.skip)
    B.idmap.save()
    log.info("== %s done: ok=%d err=%d skip=%d", sm, cnt.ok, cnt.err, cnt.skip)


def step_payments():
    def fix(vals, rec):
        if rec.get("is_internal_transfer") and "is_internal_transfer" in vals:
            vals["is_internal_transfer"] = True
        return vals

    run_linked("account.payment", "account.payment",
               skip=["move_id", "state", "payment_reference", "paired_internal_transfer_payment_id", "move_name",
                     "reconciled_invoice_ids", "reconciled_bill_ids", "reconciled_statement_line_ids", "is_reconciled",
                     "is_matched", "payment_transaction_id", "payment_token_id", "outstanding_account_id",
                     "amount_signed", "payment_method_code"],
               rename={}, must=["journal_id"], extra_vals=fix)


def step_bank():
    run_linked("account.bank.statement.line", "account.bank.statement.line",
               skip=["move_id", "statement_id", "is_reconciled", "state", "statement_complete", "statement_valid",
                     "statement_balance_end_real", "move_name", "payment_ids", "internal_index"],
               rename={}, must=[])


def _rec_dst_line(smodel_line, sid):
    return B.idmap.get("account.move.line", sid)


def step_reconcile():
    sm = "account.partial.reconcile"
    if not _model_ok(sm):
        return
    sf = B.src.fields(sm)
    fields = [f for f in ("debit_move_id", "credit_move_id", "amount", "debit_amount_currency",
                          "credit_amount_currency", "max_date", "full_reconcile_id") if f in sf]
    dom = []
    if O.date_from:
        dom.append(("max_date", ">=", O.date_from))
    if O.date_to:
        dom.append(("max_date", "<=", O.date_to))
    ids = B.src.call(sm, "search", dom, order="id", context=B.CTX)
    ids = [i for i in ids if not B.idmap.get(sm, i)]
    if O.limit:
        ids = ids[:O.limit]
    log.info("== %s : %d baqi", sm, len(ids))
    fulls, ok, skipped, err = {}, 0, 0, 0
    full_src = {}
    if B.src.fields("account.full.reconcile"):
        for r in B.src.call("account.full.reconcile", "search_read", [], fields=["exchange_move_id"], context=B.CTX):
            full_src[r["id"]] = r.get("exchange_move_id")
    for i in range(0, len(ids), 200):
        recs = B.src.call(sm, "read", ids[i:i + 200], fields=fields, context=B.CTX)
        batch, meta = [], []
        for r in recs:
            d = B.idmap.get("account.move.line", r["debit_move_id"][0])
            c = B.idmap.get("account.move.line", r["credit_move_id"][0])
            if not d or not c:
                skipped += 1
                continue
            v = {"debit_move_id": d, "credit_move_id": c, "amount": r.get("amount") or 0.0,
                 "debit_amount_currency": r.get("debit_amount_currency") or 0.0,
                 "credit_amount_currency": r.get("credit_amount_currency") or 0.0}
            if r.get("max_date"):
                v["max_date"] = r["max_date"]
            if r.get("full_reconcile_id"):
                sfid = r["full_reconcile_id"][0]
                if sfid not in fulls:
                    fid = B.idmap.get("account.full.reconcile", sfid)
                    if not fid and not O.dry:
                        fv = {}
                        ex = full_src.get(sfid)
                        if ex and rid("account.move", ex[0]):
                            fv["exchange_move_id"] = rid("account.move", ex[0])
                        fid = dcall("account.full.reconcile", "create", fv, context=CTX_DOC)
                        B.idmap.set("account.full.reconcile", sfid, fid)
                    fulls[sfid] = fid
                if fulls[sfid]:
                    v["full_reconcile_id"] = fulls[sfid]
            batch.append(v)
            meta.append(r["id"])
        if not batch or O.dry:
            ok += len(batch)
            continue
        try:
            new = bulk_create("account.partial.reconcile", batch, dict(CTX_DOC, no_exchange_difference=True))
            for s_id, d_id in zip(meta, new):
                B.idmap.set(sm, s_id, d_id)
            ok += len(new)
        except Exception as e:
            log.warning("bulk reconcile fail (%s) - aik aik karke", str(e)[:150])
            for s_id, v in zip(meta, batch):
                try:
                    B.idmap.set(sm, s_id, dcall("account.partial.reconcile", "create", v, context=CTX_DOC))
                    ok += 1
                except Exception as e2:
                    err += 1
                    failed(sm, s_id, "", e2)
        if (i // 200) % 20 == 0:
            B.idmap.save()
            log.info("[reconcile] %d/%d ok=%d skipped(lines nahi)=%d err=%d", i + len(recs), len(ids), ok, skipped, err)
    B.idmap.save()
    log.info("== reconcile done: ok=%d skipped=%d err=%d", ok, skipped, err)


SPEC_ANALYTIC_LINE = dict(model="account.analytic.line", date_field="date", must=["account_id"], batch=100)


# ------------------------------------------------------------------ STEP: payroll / attendance / leaves / expense / approvals
def find_employee_lookup(rec):
    return None


SPEC_PAYRUN = dict(
    model="hr.payslip.run", date_field="date_start", extra_read=["state", "name"], skip=["state", "slip_ids"],
    finder=lambda rec: _find_by("hr.payslip.run", [("name", "=", rec.get("name")), ("date_start", "=", rec.get("date_start"))]),
    after=lambda rec, did, ctx: write_state("hr.payslip.run", did, rec))


def after_payslip(rec, did, ctx):
    write_state("hr.payslip", did, rec, fallback={"paid": "done"})
    if rec.get("paid") and dst_has("hr.payslip", "paid"):
        try:
            dcall("hr.payslip", "write", [did], {"paid": True}, context=B.CTX)
        except Exception:
            pass


SPEC_PAYSLIP = dict(
    model="hr.payslip", date_field="date_from", must=["employee_id"], extra_read=["state", "number", "paid", "name"],
    skip=["state", "line_ids", "worked_days_line_ids", "input_line_ids", "net_wage", "basic_wage", "compute_date"],
    finder=lambda rec: _find_by("hr.payslip", [("number", "=", rec.get("number"))]) if rec.get("number") else None,
    after=after_payslip,
    lines=[dict(model="hr.payslip.line", parent="slip_id", inline="line_ids", must=["salary_rule_id"],
                skip=["slip_id", "total"], inline_parent="slip_id"),
           dict(model="hr.payslip.worked_days", parent="payslip_id", inline="worked_days_line_ids",
                skip=["payslip_id"], inline_parent="payslip_id"),
           dict(model="hr.payslip.input", parent="payslip_id", inline="input_line_ids",
                skip=["payslip_id"], inline_parent="payslip_id")])

SPEC_ATT = dict(
    model="hr.attendance", date_field="check_in", must=["employee_id"], batch=100,
    skip=["overtime_hours", "worked_hours", "validated_overtime_hours", "overtime_status"],
    finder=lambda rec: _find_by("hr.attendance", [("employee_id", "=", rid("hr.employee", rec["employee_id"][0])),
                                                  ("check_in", "=", rec.get("check_in"))])
    if rec.get("employee_id") and rid("hr.employee", rec["employee_id"][0]) else None,
    extra_read=["employee_id", "check_in"])

LEAVE_CTX = {"leave_fast_create": True, "leave_skip_state_check": True, "import_file": True, "mail_activity_automation_skip": True}
SPEC_ALLOC = dict(
    model="hr.leave.allocation", must=["employee_id"], ctx=LEAVE_CTX, extra_read=["state"], skip=["state"],
    after=lambda rec, did, ctx: write_state("hr.leave.allocation", did, rec))
SPEC_LEAVE = dict(
    model="hr.leave", date_field="date_from", must=["employee_id"], ctx=LEAVE_CTX, extra_read=["state"], skip=["state"],
    after=lambda rec, did, ctx: write_state("hr.leave", did, rec))

SPEC_SHEET = dict(
    model="hr.expense.sheet", must=["employee_id"], extra_read=["state", "name"], skip=["state", "expense_line_ids"],
    finder=lambda rec: _find_by("hr.expense.sheet", [("name", "=", rec.get("name"))]))
SPEC_EXPENSE = dict(model="hr.expense", must=["employee_id"], extra_read=["name"],
                    skip=["state"])


def finalize_sheets():
    sm = "hr.expense.sheet"
    if not _model_ok(sm) or O.dry:
        return
    for r in B.src.call(sm, "search_read", [], fields=["state", "name"], context=B.CTX):
        d = B.idmap.get(sm, r["id"])
        if d:
            write_state(sm, d, r)


SPEC_APPROVAL = dict(
    model="approval.request", must=["category_id"], extra_read=["request_status", "name"],
    skip=["request_status", "approver_ids"],
    after=lambda rec, did, ctx: write_state("approval.request", did, rec, field="request_status"),
    lines=[dict(model="approval.approver", parent="request_id", inline="approver_ids", skip=["request_id", "status"],
                inline_parent="request_id"),
           dict(model="approval.product.line", parent="approval_request_id", inline="product_line_ids",
                skip=["approval_request_id"], inline_parent="approval_request_id")])


# ------------------------------------------------------------------ optional: fleet / POS / stock opening / attachments
FLEET_STEPS = [
    dict(model="fleet.vehicle.state", key=["name"]), dict(model="fleet.service.type", key=["name"]),
    dict(model="fleet.vehicle.model.brand", key=["name"]),
    dict(model="fleet.vehicle.model", key=["name", "brand_id"]),
    dict(model="fleet.vehicle", key=["license_plate"]),
    dict(model="fleet.vehicle.odometer"), dict(model="fleet.vehicle.log.contract"),
]


def step_fleet():
    ensure_masters(["hr.employee"], quiet=True)
    for st in FLEET_STEPS:
        B.DRY = O.dry
        B.run_step(st)
    B.flush_pending()
    B.idmap.save()


def after_pos_session(rec, did, ctx):
    fixes = {k: rec[k] for k in ("name",) if rec.get(k) and dst_has("pos.session", k)}
    if fixes:
        try:
            dcall("pos.session", "write", [did], fixes, context=B.CTX)
        except Exception:
            pass
    write_state("pos.session", did, rec)


def after_pos_order(rec, did, ctx):
    fixes = {k: rec[k] for k in ("name", "pos_reference") if rec.get(k) and dst_has("pos.order", k)}
    if fixes:
        try:
            dcall("pos.order", "write", [did], fixes, context=B.CTX)
        except Exception:
            pass
    write_state("pos.order", did, rec)


SPEC_POS_SESSION = dict(model="pos.session", date_field="start_at", serial=True, must=["config_id"],
                        extra_read=["state", "name"], skip=["state", "order_ids", "statement_line_ids"],
                        after=after_pos_session)
SPEC_POS_ORDER = dict(
    model="pos.order", date_field="date_order", must=["session_id"], extra_read=["state", "name", "pos_reference"],
    skip=["state", "lines", "payment_ids", "amount_tax", "amount_total", "amount_paid", "amount_return"],
    after=after_pos_order,
    finder=lambda rec: _find_by("pos.order", [("pos_reference", "=", rec.get("pos_reference"))]) if rec.get("pos_reference") else None,
    lines=[dict(model="pos.order.line", parent="order_id", inline="lines", skip=["order_id", "pack_lot_ids", "refunded_orderline_id"],
                inline_parent="order_id"),
           dict(model="pos.payment", parent="pos_order_id", inline="payment_ids", must=["payment_method_id"],
                skip=["pos_order_id"], inline_parent="pos_order_id")])


def step_stock_opening():
    """Stock history (pickings/moves) nahi - sirf maujooda quantity opening inventory ke taur par"""
    sm = "stock.quant"
    if not _model_ok(sm):
        return
    ensure_masters(["stock.location", "product.product"], quiet=True)
    quants = B.src.call(sm, "search_read", [("location_id.usage", "=", "internal")],
                        fields=["product_id", "location_id", "quantity", "lot_id"], context=B.CTX)
    log.info("== stock opening: %d quant lines", len(quants))
    agg, bad = {}, 0
    for q in quants:
        p, l = rid("product.product", q["product_id"][0]), rid("stock.location", q["location_id"][0])
        lot = q.get("lot_id")
        if not p or not l or lot:
            bad += 1
            if lot:
                dropped(sm, q["id"], "lot_id", lot, "lot/serial quant - manual (lots migrate nahi hue)")
            continue
        agg[(p, l)] = agg.get((p, l), 0.0) + q["quantity"]
    log.info("lines ban sakti hain: %d, skip: %d", len(agg), bad)
    if O.dry:
        return
    ctx = dict(B.CTX, inventory_mode=True)
    items = [(k, v) for k, v in agg.items() if abs(v) > 1e-9]
    if O.limit:
        items = items[:O.limit]
    done = []
    for i in range(0, len(items), 100):
        vl = [{"product_id": p, "location_id": l, "inventory_quantity": qty} for (p, l), qty in items[i:i + 100]]
        try:
            done += bulk_create(sm, vl, ctx)
        except Exception as e:
            log.error("quant batch fail: %s", str(e)[:200])
    for i in range(0, len(done), 100):
        try:
            dcall(sm, "action_apply_inventory", ids=done[i:i + 100], context=ctx)
        except Exception as e:
            log.error("apply inventory fail: %s", str(e)[:200])
    log.info("stock opening: %d quants apply hue", len(done))


ATT_MODELS = ["account.move", "sale.order", "purchase.order", "hr.expense", "hr.payslip", "crm.lead", "hr.employee"]


def step_attachments():
    sm = "ir.attachment"
    for res_model in ATT_MODELS:
        ids = B.src.call(sm, "search", [("res_model", "=", res_model), ("res_field", "=", False), ("type", "=", "binary")],
                         order="id", context=B.CTX)
        ids = [i for i in ids if not B.idmap.get("ir.attachment.tx", i)]
        if O.limit:
            ids = ids[:O.limit]
        log.info("== attachments %s: %d", res_model, len(ids))
        ok = 0
        for i in range(0, len(ids), 5):
            for r in B.src.call(sm, "read", ids[i:i + 5], fields=["name", "datas", "mimetype", "res_id", "description"], context=B.CTX):
                d = B.idmap.get(res_model, r["res_id"])
                if not d or not r.get("datas"):
                    continue
                if O.dry:
                    ok += 1
                    continue
                try:
                    n = dcall(sm, "create", {"name": r["name"], "datas": r["datas"], "mimetype": r.get("mimetype") or False,
                                             "res_model": res_model, "res_id": d, "type": "binary"}, context=B.CTX)
                    B.idmap.d.setdefault("ir.attachment.tx", {})[str(r["id"])] = n
                    ok += 1
                except Exception as e:
                    failed(sm, r["id"], r["name"], e)
            if (i // 5) % 50 == 0:
                B.idmap.save()
        log.info("attachments %s ok=%d", res_model, ok)


# ------------------------------------------------------------------ step registry
def S(spec):
    return lambda: run_doc(spec)


def _crm_spec():
    return dict(model="crm.lead", extra_read=["name"],
                finder=lambda rec: _find_by("crm.lead", [("name", "=", rec.get("name"))]))


STEPS = {
    "masters": (step_masters, "utm, crm stages/tags, analytic accounts, rental pricing, approval categories + sab masters auto-map"),
    "crm": (lambda: run_doc(_crm_spec()), "crm.lead (leads/opportunities)"),
    "sales": (S(SPEC_SALE), "sale.order + lines (rental orders bhi isi mein)"),
    "purchase": (lambda: [run_doc(SPEC_PREQ), run_doc(SPEC_PO), run_doc(dict(SPEC_PREQ_ALLOC))],
                 "purchase.request, purchase.order + lines, request allocations"),
    "moves": (S(SPEC_MOVE), "account.move + lines: invoices, bills, refunds, journal entries (payment/bank moves draft rehte hain)"),
    "payments": (step_payments, "account.payment (move se link + post)"),
    "bank": (step_bank, "account.bank.statement.line (move se link + post)"),
    "reconcile": (step_reconcile, "account.partial.reconcile / full.reconcile (invoice-payment matching)"),
    "analytic": (S(SPEC_ANALYTIC_LINE), "account.analytic.line"),
    "payroll": (lambda: [run_doc(SPEC_PAYRUN), run_doc(SPEC_PAYSLIP)], "hr.payslip.run, hr.payslip + lines/worked days/inputs"),
    "attendance": (S(SPEC_ATT), "hr.attendance"),
    "leaves": (lambda: [run_doc(SPEC_ALLOC), run_doc(SPEC_LEAVE)], "hr.leave.allocation, hr.leave"),
    "expense": (lambda: [run_doc(SPEC_SHEET), run_doc(SPEC_EXPENSE), finalize_sheets()], "hr.expense.sheet + hr.expense"),
    "approvals": (S(SPEC_APPROVAL), "approval.request + approvers"),
    # optional (core mein shamil nahi)
    "fleet": (step_fleet, "[optional] fleet vehicles, odometer, contracts"),
    "pos": (lambda: [run_doc(SPEC_POS_SESSION), run_doc(SPEC_POS_ORDER)], "[optional] pos.session, pos.order + lines + payments (workers=1)"),
    "stock_opening": (step_stock_opening, "[optional] stock.quant -> opening inventory (history nahi)"),
    "attachments": (step_attachments, "[optional] invoices/orders/payslips ki attachments"),
}
CORE = ["masters", "crm", "sales", "purchase", "moves", "payments", "bank", "reconcile", "analytic",
        "payroll", "attendance", "leaves", "expense", "approvals"]


# ------------------------------------------------------------------ check / verify
def cmd_check(fix):
    log.info("== MASTER CHECK (idmap + natural-key auto-map)  fix=%s", fix)
    rows = []
    for sm in NEED:
        cfg = AUTOMAP.get(sm)
        if sm == "res.partner":
            n_src = B.src.call(sm, "search_count", [], context=B.CTX)
            n_map = len(B.idmap.d.get(sm, {}))
            rows.append((sm, n_src, n_map, n_src - n_map, "partners idmap mein hain" if n_src - n_map <= 100 else
                         "run_migration.sh 5 dobara chalao"))
            continue
        if not cfg:
            continue
        r = automap(sm, cfg, apply=fix)
        if r is None:
            rows.append((sm, "-", "-", "-", "src mein model nahi (is se koi masla nahi)"))
        elif r == "dst-model-nahi":
            rows.append((sm, "-", "-", "-", "Odoo 20 mein model nahi"))
        else:
            tot, already, new, miss = r
            note = "OK" if not miss else ("MISSING: " + ", ".join(str(x[1])[:25] for x in miss[:4]))
            rows.append((sm, tot, already + (new if fix else 0), len(miss), note + ("" if not new or fix else
                                                                                       "  (%d auto-map ho sakte hain: --fix)" % new)))
    if fix:
        B.idmap.save()
    print("\n%-32s %8s %8s %8s  %s" % ("MODEL", "SRC", "MAPPED", "MISSING", "NOTE"))
    for r in rows:
        print("%-32s %8s %8s %8s  %s" % r)
    print("\nMISSING wale masters pehle purane script se migrate karo (./run_migration.sh), phir ye check dobara chalao.")
    if os.path.exists("scan_report.csv"):
        handled = {"account.move", "account.move.line", "sale.order", "sale.order.line", "crm.lead", "hr.payslip",
                   "hr.attendance", "purchase.order", "account.payment", "pos.order"}
        print("\nscan_report.csv mein jin models mein data hai magar ye script unhe migrate NAHI karti (info):")
        with open("scan_report.csv", encoding="utf-8") as fh:
            for r in list(csv.DictReader(fh))[:70]:
                m = r["model"]
                if m in handled or m.startswith(("report.", "mail.", "ir.", "res.", "product.", "website.", "stock.report")) \
                        or m.endswith(".report"):
                    continue
                if m in ("stock.picking", "stock.move", "stock.move.line", "stock.valuation.layer", "hr.work.entry",
                         "account.invoice.report", "sale.report", "procurement.group", "loyalty.card"):
                    print("   %-34s %s   <- jaan-boojh kar nahi (neeche README dekho)" % (m, r["count"]))


def cmd_verify():
    log.info("== VERIFY")
    pairs = [("crm.lead", []), ("sale.order", []), ("sale.order.line", []), ("purchase.order", []),
             ("account.move", []), ("account.move.line", []), ("account.payment", []),
             ("account.bank.statement.line", []), ("account.partial.reconcile", []), ("hr.payslip", []),
             ("hr.attendance", []), ("hr.leave", []), ("hr.leave.allocation", []), ("hr.expense", []),
             ("hr.expense.sheet", []), ("approval.request", []), ("pos.order", []), ("pos.session", [])]
    print("\n%-30s %10s %10s" % ("MODEL", "SRC", "MIGRATED(idmap_tx)"))
    for m, dom in pairs:
        if B.src.fields(m) is None:
            continue
        n = B.src.call(m, "search_count", dom, context=B.CTX)
        if n:
            print("%-30s %10d %10d%s" % (m, n, len(B.idmap.t.get(m, {})), "" if len(B.idmap.t.get(m, {})) >= n else "   <-- kam"))
    # trial balance
    af = B.src.fields("account.account")
    if af and B.src.fields("account.move.line"):
        rows = B.src.call("account.move.line", "read_group", [("parent_state", "=", "posted")],
                          ["account_id", "debit:sum", "credit:sum"], ["account_id"], lazy=False, context=B.CTX)
        drows = None
        try:
            drows = raw_json2("account.move.line", "read_group",
                              {"domain": [["parent_state", "=", "posted"]], "fields": ["debit:sum", "credit:sum"],
                               "groupby": ["account_id"], "lazy": False, "context": B.CTX})
        except Exception:
            fr = raw_json2("account.move.line", "formatted_read_group",
                           {"domain": [["parent_state", "=", "posted"]], "groupby": ["account_id"],
                            "aggregates": ["debit:sum", "credit:sum"], "context": B.CTX})
            drows = [{"account_id": r["account_id"], "debit": r.get("debit:sum"), "credit": r.get("credit:sum")} for r in fr]
        dmap = {r["account_id"][0]: r for r in drows if r.get("account_id")}
        bad, out = 0, []
        for r in rows:
            sa = r["account_id"][0]
            da = rid("account.account", sa)
            dr = dmap.get(da, {})
            dd, dc = round(dr.get("debit", 0) or 0, 2), round(dr.get("credit", 0) or 0, 2)
            sd, sc = round(r.get("debit", 0) or 0, 2), round(r.get("credit", 0) or 0, 2)
            if abs(dd - sd) > 0.5 or abs(dc - sc) > 0.5:
                bad += 1
                out.append((r["account_id"][1], sd, dd, sc, dc))
        with open("tx_verify_trial_balance.csv", "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["account", "src_debit", "dst_debit", "src_credit", "dst_credit"])
            w.writerows(out)
        print("\nTrial balance: %d accounts mein farq (date filter se migrate kiya ho to expected) -> tx_verify_trial_balance.csv" % bad)
    for f, why in ((FAILED_CSV, "fail hue records"), (MISMATCH_CSV, "amount/lines mismatch"), (DROPPED_CSV, "chhode hue links")):
        if os.path.exists(f):
            print("%-22s %6d rows  (%s)" % (f, sum(1 for _ in open(f, encoding="utf-8")) - 1, why))
    if _dropped_n:
        print("is run ke dropped summary:", dict(list(_dropped_n.items())[:10]))


# ------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["list", "check", "run", "verify"])
    ap.add_argument("--steps", default="", help="comma separated: step names ya 'core' ya 'all'")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0, help="har model ke sirf pehle N records (test ke liye)")
    ap.add_argument("--workers", type=int, default=1, help="parallel threads (2-6 theek; POS ke liye hamesha 1)")
    ap.add_argument("--date-from")
    ap.add_argument("--date-to")
    ap.add_argument("--post-method", choices=["state", "action"], default="state")
    ap.add_argument("--fix", action="store_true", help="check ke saath: mil jane wale masters idmap mein likh do")
    a = ap.parse_args()
    if a.cmd == "list":
        for k, (_, d) in STEPS.items():
            print("%-14s %s%s" % (k, d, "" if k in CORE else ""))
        print("\ncore = %s" % ",".join(CORE))
        return
    O.dry, O.limit, O.workers = a.dry_run, a.limit, a.workers
    O.date_from, O.date_to, O.post_method = a.date_from, a.date_to, a.post_method
    B.DRY = O.dry
    B.src, B.dst = B.Odoo(B.SRC), B.Odoo2(B.DST)
    B.idmap = TxIdMap(B.IDMAP_FILE, TX_IDMAP_FILE)
    # src.call ko thread-safe banao (xmlrpc ServerProxy shared hai)
    _orig = B.src.call

    def locked(*args, **kw):
        with SRC_LOCK:
            return _orig(*args, **kw)

    B.src.call = locked
    B.seed_companies()
    if a.cmd == "check":
        return cmd_check(a.fix)
    if a.cmd == "verify":
        return cmd_verify()
    names = []
    for s in [x.strip() for x in a.steps.split(",") if x.strip()]:
        names += CORE if s == "core" else (list(STEPS) if s == "all" else [s])
    if not names:
        raise SystemExit("--steps do (jaise --steps crm,sales ya --steps core). 'list' se names dekho.")
    for n in names:
        if n not in STEPS:
            raise SystemExit("step '%s' nahi mila. list dekho." % n)
    if not O.dry:
        import shutil
        for f in (B.IDMAP_FILE, TX_IDMAP_FILE):
            if os.path.exists(f):
                shutil.copy(f, f + ".before_tx_" + time.strftime("%Y%m%d_%H%M%S"))
    if any(n not in ("masters",) for n in names):
        ensure_masters(quiet=True)          # employees/taxes/contracts wagera idmap mein na hon to natural key se jodo
    for n in names:
        log.info("################ STEP %s ################", n)
        STEPS[n][0]()
        B.idmap.save()
    log.info("TX DONE. tx_failed.csv / tx_mismatch.csv / tx_dropped.csv dekho, phir: python3 migrate_transactions.py verify")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        try:
            B.idmap.save()
        except Exception:
            pass
        log.warning("Ruk gaya (Ctrl+C) - idmap save ho gaya, dobara chalao to wahin se chalega")