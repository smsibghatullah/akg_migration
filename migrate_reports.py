#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Odoo 16 -> Odoo 20 : PDF report format (sale order, invoice, ...)      *** STANDALONE ***
Ye script migrate_transactions.py aur txmap.sqlite3 se bilkul alag hai (koi map/database file nahi likhti).

  python3 migrate_reports.py list                         # DRY-RUN: kya hai aur kya hoga (dst mein kuch nahi likhta)
  python3 migrate_reports.py run --apply                  # asal mein karta hai
  python3 migrate_reports.py run --apply --inactive       # custom templates inactive bana kar (ek ek karke test karo)
  python3 migrate_reports.py run --apply --only paperformats,companies
  python3 migrate_reports.py run --models sale.order,account.move,purchase.order --roots purchase.report_purchaseorder

Connection: isi folder ki local_config.py (SRC, DST dicts) se, warna migrate_transactions.py ki CONFIG se (sirf padhta hai).

Kya karta hai (--only se chun sakte ho):
  paperformats : report.paperformat       - naam se mel, na ho to create
  companies    : company ki document layout (logo, header/footer, primary/secondary color, font, background,
                 paperformat, external layout)  - company naam se mel
  reports      : CUSTOM report actions (jo Odoo 20 mein nahi) create. Standard reports ke sirf farq dikhata hai.
  views        : CUSTOM QWeb templates (Studio / custom module wale, report templates ke children) create

Jo STANDARD template source mein badla gaya tha, wo copy NAHI hota (16 aur 20 ke standard templates alag hain);
unka source arch report_views_src/ folder mein dump hota hai taake haath se port kar sako.
Report: reports_migration.csv.   PEHLE DATABASE BACKUP.
"""
import sys
import csv
import json
import time
import argparse
import collections
import threading
import xmlrpc.client
import urllib.request
import urllib.error
from pathlib import Path

try:
    from local_config import SRC, DST
except ImportError:
    try:
        from migrate_transactions import SRC, DST      # sirf config padhta hai
    except ImportError:
        sys.exit("Config nahi mili: local_config.py (SRC, DST) banao ya migrate_transactions.py isi folder mein rakho.")

CTX = {"active_test": False}
SKIP_FIELDS = {"id", "create_uid", "create_date", "write_uid", "write_date", "display_name", "__last_update"}
ROOTS = {
    "sale.order": ["sale.report_saleorder", "sale.report_saleorder_document"],
    "account.move": ["account.report_invoice"],
    "purchase.order": ["purchase.report_purchaseorder"],
    "stock.picking": ["stock.report_delivery", "stock.report_picking"],
}
LAYOUT_ROOTS = ["web.external_layout", "web.address_layout", "web.internal_layout", "web.html_container"]
PF = "report.paperformat"
ROWS = []
FIELD_ATTRS = ["type", "relation", "store", "readonly", "required", "selection"]


def m2o_id(v):
    return v[0] if isinstance(v, (list, tuple)) else v


# ----------------------------------------------------------------- connections
class Source:
    def __init__(self):
        self._f, self._lock = {}, threading.Lock()
        common = xmlrpc.client.ServerProxy(SRC["url"].rstrip("/") + "/xmlrpc/2/common", allow_none=True)
        self.uid = common.authenticate(SRC["db"], SRC["user"], SRC["password"], {})
        if not self.uid:
            raise RuntimeError("Odoo 16 login fail")
        self.proxy = xmlrpc.client.ServerProxy(SRC["url"].rstrip("/") + "/xmlrpc/2/object", allow_none=True)

    def call(self, model, method, *args, **kw):
        for attempt in range(4):
            try:
                return self.proxy.execute_kw(SRC["db"], self.uid, SRC["password"], model, method, list(args), kw)
            except (OSError, xmlrpc.client.ProtocolError):
                if attempt == 3:
                    raise
                time.sleep(2 * (attempt + 1))

    def fields(self, model):
        with self._lock:
            if model not in self._f:
                try:
                    self._f[model] = self.call(model, "fields_get", attributes=FIELD_ATTRS)
                except Exception:
                    self._f[model] = None
            return self._f[model]


class Dest:
    POS = {"search": ["domain"], "search_read": ["domain"], "search_count": ["domain"], "read": ["ids"],
           "write": ["ids", "vals"], "create": ["vals_list"], "unlink": ["ids"]}

    def __init__(self):
        self._f, self._lock = {}, threading.Lock()
        self.call("res.users", "context_get")

    def call(self, model, method, *args, **kw):
        body = dict(kw)
        for n, v in zip(self.POS.get(method, []), args):
            body[n] = [v] if n == "vals_list" else v
        data = json.dumps(body).encode()
        for attempt in range(4):
            req = urllib.request.Request(
                "%s/json/2/%s/%s" % (DST["url"].rstrip("/"), model, method), data=data, method="POST",
                headers={"Authorization": "bearer " + DST["api_key"], "X-Odoo-Database": DST["db"],
                         "Content-Type": "application/json", "User-Agent": "akg-reports"})
            try:
                with urllib.request.urlopen(req, timeout=600) as r:
                    raw = r.read().decode()
                res = json.loads(raw) if raw else None
                return res[0] if method == "create" and isinstance(res, list) else res
            except urllib.error.HTTPError as e:
                txt = e.read().decode("utf-8", "replace")
                msg = txt
                try:
                    msg = json.loads(txt).get("message", txt)
                except Exception:
                    pass
                raise RuntimeError("HTTP %s %s/%s: %s" % (e.code, model, method, str(msg)[:500]))
            except (urllib.error.URLError, TimeoutError, ConnectionError):
                if attempt == 3:
                    raise
                time.sleep(2 * (attempt + 1))

    def fields(self, model):
        with self._lock:
            if model not in self._f:
                try:
                    self._f[model] = self.call(model, "fields_get", attributes=FIELD_ATTRS)
                except Exception:
                    self._f[model] = None
            return self._f[model]


S = D = None


def row(kind, sid, name, action, detail=""):
    ROWS.append((kind, sid, name, action, detail))
    print("  [%-12s] %-11s %-48s %s" % (kind, action, str(name)[:48], detail[:400]))


def installed_dst_modules():
    try:
        return {r["name"] for r in D.call("ir.module.module", "search_read", [("state", "=", "installed")],
                                          fields=["name"], context=CTX)}
    except Exception:
        return None


def xmlids(conn, model):
    out = collections.defaultdict(list)
    for r in conn.call("ir.model.data", "search_read", [("model", "=", model)],
                       fields=["module", "name", "res_id"], context=CTX):
        out[r["res_id"]].append((r["module"], r["name"]))
    return out


def is_custom(xids, dst_mods):
    """custom tab hi hai jab us ke SAARE xmlid studio ke ya dst mein na-maujood module ke hon (ya koi xmlid na ho)"""
    if not xids:
        return True
    return all(x[0] == "studio_customization" or (dst_mods is not None and x[0] not in dst_mods) for x in xids)


def dst_view_by_key(key):
    if not key:
        return None
    f = D.call("ir.ui.view", "search", [("key", "=", key)], context=CTX)
    return f[0] if f else None


# ----------------------------------------------------------------- paperformats
PF_MAP = {}


def scalar_vals(rec, sf, df):
    vals = {}
    for f, v in rec.items():
        s = sf.get(f)
        if f in SKIP_FIELDS or f not in df or not s or s["type"] in ("one2many", "many2one", "many2many", "binary"):
            continue
        if v is False and s["type"] != "boolean":
            continue
        if s["type"] == "selection":
            allowed = [x[0] for x in (df[f].get("selection") or [])]
            if allowed and v not in allowed:
                continue
        vals[f] = v
    return vals


def do_paperformats(a):
    print("\n== paperformats")
    sf, df = S.fields(PF), D.fields(PF)
    if sf is None or df is None:
        print("  model nahi mila")
        return
    fl = [f for f, s in sf.items() if s.get("store") and s["type"] not in ("one2many", "many2one", "many2many", "binary")
          and f not in SKIP_FIELDS]
    for r in S.call(PF, "search_read", [], fields=fl, context=CTX):
        found = D.call(PF, "search", [("name", "=", r["name"])], context=CTX)
        if found:
            PF_MAP[r["id"]] = found[0]
            row("paperformat", r["id"], r["name"], "EXISTS", "dst #%s" % found[0])
            continue
        if not a.apply:
            row("paperformat", r["id"], r["name"], "WILL_ADD", "")
            continue
        try:
            did = D.call(PF, "create", scalar_vals(r, sf, df), context=CTX)
            PF_MAP[r["id"]] = did
            row("paperformat", r["id"], r["name"], "CREATED", "dst #%s" % did)
        except Exception as e:
            row("paperformat", r["id"], r["name"], "ERROR", str(e)[:200])


# ----------------------------------------------------------------- company layout
def dst_paperformat_for(src_pf_id):
    """Source paperformat ka dst id (naam se); run mein pehle paperformats step na chala ho tab bhi kaam kare."""
    if src_pf_id in PF_MAP:
        return PF_MAP[src_pf_id]
    name = S.call(PF, "read", [src_pf_id], fields=["name"], context=CTX)[0]["name"]
    f = D.call(PF, "search", [("name", "=", name)], context=CTX)
    if f:
        PF_MAP[src_pf_id] = f[0]
    return f[0] if f else None


def do_companies(a):
    print("\n== companies (document layout)")
    sf, df = S.fields("res.company"), D.fields("res.company")
    want = ["external_report_layout_id", "report_header", "report_footer", "paperformat_id", "logo", "primary_color",
            "secondary_color", "font", "layout_background", "layout_background_image", "company_details"]
    cf = [f for f in want if f in sf and f in df]
    dcomps = D.call("res.company", "search_read", [], fields=["name"], context=CTX)
    by_name = {c["name"].strip().lower(): c["id"] for c in dcomps}
    scomps = S.call("res.company", "read", S.call("res.company", "search", [], context=CTX), fields=cf + ["name"],
                    context=CTX)
    for c in scomps:
        did = by_name.get(c["name"].strip().lower())
        if not did and len(dcomps) == 1 and len(scomps) == 1:
            did = dcomps[0]["id"]
        if not did:
            row("company", c["id"], c["name"], "SKIP", "dst mein is naam ki company nahi")
            continue
        vals = {}
        for f in cf:
            v = c.get(f)
            if f == "external_report_layout_id":
                if v:
                    k = a.layout or S.call("ir.ui.view", "read", [m2o_id(v)], fields=["key"], context=CTX)[0].get("key")
                    nv = dst_view_by_key(k)
                    if nv:
                        vals[f] = nv
                    else:
                        cands = [r["key"] for r in D.call("ir.ui.view", "search_read", [("key", "=like", "web.%layout%")],
                                                         fields=["key"], context=CTX)]
                        row("company", c["id"], c["name"], "WARN",
                            "layout dst mein nahi mila: %s | dst mein ye hain: %s | --layout KEY se chuno" % (
                                k, ", ".join(sorted(cands))))
            elif f == "paperformat_id":
                if v:
                    nv = dst_paperformat_for(m2o_id(v))
                    if nv:
                        vals[f] = nv
                    else:
                        row("company", c["id"], c["name"], "WARN", "paperformat dst mein nahi (pehle paperformats step)")
            elif f in ("font", "layout_background") and v:
                allowed = [x[0] for x in (df[f].get("selection") or [])]
                if not allowed or v in allowed:
                    vals[f] = v
                else:
                    row("company", c["id"], c["name"], "WARN", "%s=%s dst mein allowed nahi" % (f, v))
            elif v not in (None, False, ""):
                vals[f] = v
        if not vals:
            row("company", c["id"], c["name"], "SKIP", "kuch copy karne ko nahi")
            continue
        names = ", ".join(sorted(vals))
        if not a.apply:
            row("company", c["id"], c["name"], "WILL_SET", names)
            continue
        try:
            D.call("res.company", "write", [did], vals, context=CTX)
            row("company", c["id"], c["name"], "UPDATED", names)
        except Exception as e:
            row("company", c["id"], c["name"], "ERROR", str(e)[:200])


# ----------------------------------------------------------------- report actions
def do_reports(a):
    print("\n== reports (ir.actions.report)")
    R = "ir.actions.report"
    sf, df = S.fields(R), D.fields(R)
    fl = [f for f in ("name", "model", "report_type", "report_name", "report_file", "print_report_name", "attachment",
                      "attachment_use", "multi", "paperformat_id", "binding_type") if f in sf]
    xm = xmlids(S, R)
    dst_mods = installed_dst_modules()
    for r in S.call(R, "search_read", [("model", "in", a.models), ("report_type", "in", ["qweb-pdf", "qweb-html"])],
                    fields=fl, context=CTX):
        cmp_f = [f for f in ("print_report_name", "attachment", "attachment_use") if f in df]
        found = D.call(R, "search_read", [("report_name", "=", r["report_name"])], fields=cmp_f, context=CTX)
        if found:
            diff = [f for f in cmp_f if (found[0].get(f) or False) != (r.get(f) or False)]
            row("report", r["id"], r["report_name"], "EXISTS", ("farq: " + ", ".join(diff)) if diff else "")
            continue
        if not is_custom(xm.get(r["id"]), dst_mods):
            row("report", r["id"], r["report_name"], "STD_MISSING", "standard report hai magar dst mein nahi (module?)")
            continue
        if not a.apply:
            row("report", r["id"], r["report_name"], "WILL_ADD", r["name"])
            continue
        try:
            vals = {f: r[f] for f in fl if f not in ("paperformat_id", "binding_type") and r.get(f) is not None}
            if r.get("paperformat_id"):
                pfid = dst_paperformat_for(m2o_id(r["paperformat_id"]))
                if pfid:
                    vals["paperformat_id"] = pfid
            mid = D.call("ir.model", "search", [("model", "=", r["model"])], context=CTX)
            if mid:
                vals["binding_model_id"] = mid[0]
                vals["binding_type"] = r.get("binding_type") or "report"
            did = D.call(R, "create", vals, context=CTX)
            row("report", r["id"], r["report_name"], "CREATED", "dst #%s" % did)
        except Exception as e:
            row("report", r["id"], r["report_name"], "ERROR", str(e)[:200])


# ----------------------------------------------------------------- QWeb templates
def key_of(v, xm):
    return v.get("key") or ("%s.%s" % xm[v["id"]][0] if v["id"] in xm else None)


def do_views(a):
    print("\n== views (QWeb templates)")
    V = "ir.ui.view"
    sf = S.fields(V)
    fl = [f for f in ("name", "key", "inherit_id", "active", "priority", "mode", "arch_db", "arch_updated",
                      "customize_show") if f in sf]
    xm = xmlids(S, V)
    views = {v["id"]: v for v in S.call(V, "search_read", [("type", "=", "qweb")], fields=fl, order="id", context=CTX)}
    prefixes = list(LAYOUT_ROOTS) + list(a.roots)
    for m in a.models:
        prefixes += ROOTS.get(m, [])
    children = collections.defaultdict(list)
    for v in views.values():
        if v.get("inherit_id"):
            children[m2o_id(v["inherit_id"])].append(v["id"])
    depth, queue = {}, []
    for v in views.values():
        k = key_of(v, xm)
        if k and any(k.startswith(p) for p in prefixes):
            depth[v["id"]] = 0
            queue.append(v["id"])
    while queue:
        cur = queue.pop(0)
        for ch in children.get(cur, []):
            if ch not in depth:
                depth[ch] = depth[cur] + 1
                queue.append(ch)
    dst_mods = installed_dst_modules()
    print("  report se jude QWeb views: %d (source)" % len(depth))
    dump = Path("report_views_src")
    to_create, manual = [], []
    for vid in depth:
        v = views[vid]
        if is_custom(xm.get(vid), dst_mods):
            to_create.append(v)
        elif v.get("arch_updated"):
            manual.append(v)
    for v in manual:
        dump.mkdir(exist_ok=True)
        fn = (key_of(v, xm) or v["name"]).replace("/", "_") + ".xml"
        (dump / fn).write_text(v.get("arch_db") or "", encoding="utf-8")
        row("view", v["id"], key_of(v, xm) or v["name"], "MANUAL", "standard template badla gaya tha - %s/%s" % (dump, fn))
    create_ids = {v["id"] for v in to_create}
    created = {}
    for v in sorted(to_create, key=lambda x: depth[x["id"]]):
        key = key_of(v, xm)
        label = key or v["name"]
        exists = dst_view_by_key(key) if key else None
        if exists:
            created[v["id"]] = exists
            row("view", v["id"], label, "EXISTS", "dst #%s" % exists)
            continue
        pid = None
        if v.get("inherit_id"):
            psid = m2o_id(v["inherit_id"])
            pid = created.get(psid)
            if not pid:
                pv = views.get(psid)
                pid = dst_view_by_key(key_of(pv, xm)) if pv else None
            if not pid and not (not a.apply and psid in create_ids):
                row("view", v["id"], label, "ERROR",
                    "parent dst mein nahi: %s" % (key_of(views[psid], xm) if psid in views else psid))
                continue
        if not a.apply:
            row("view", v["id"], label, "WILL_ADD", "depth %d" % depth[v["id"]])
            continue
        vals = {"name": v["name"], "type": "qweb", "arch_db": v.get("arch_db") or "<t/>",
                "active": bool(v.get("active")) and not a.inactive, "priority": v.get("priority") or 16,
                "mode": "extension" if pid else "primary"}
        if pid:
            vals["inherit_id"] = pid
        if key:
            vals["key"] = key
        if "customize_show" in v:
            vals["customize_show"] = bool(v["customize_show"])
        try:
            did = D.call(V, "create", vals, context=CTX)
            created[v["id"]] = did
            row("view", v["id"], label, "CREATED", "dst #%s%s" % (did, "" if vals["active"] else " (inactive)"))
        except Exception as e:
            dump.mkdir(exist_ok=True)
            fn = (key or v["name"]).replace("/", "_") + ".xml"
            (dump / fn).write_text(v.get("arch_db") or "", encoding="utf-8")
            row("view", v["id"], label, "ERROR", "%s | source arch: %s/%s" % (str(e)[:150], dump, fn))


# ----------------------------------------------------------------- clone: Odoo 16 ke standard templates -> Odoo 20 mein NAYE naam se
CLONE_SETS = {
    "sale.order": ["sale.report_saleorder_document", "sale.report_saleorder"],
    "account.move": ["account.report_invoice_document", "account.report_invoice", "account.report_invoice_with_payments"],
}
# Odoo 16 ke field naam jo Odoo 20 mein badal gaye (QWeb ke andar)
REWRITES = [(r"(\bline\.)tax_id\b", r"\1tax_ids"), (r"(\bline\.)product_uom\b", r"\1product_uom_id")]


def newkey(k):
    return "odoo16." + k.replace(".", "_")


def clone(a):
    import re
    print("\n== clone (Odoo 16 templates -> Odoo 20, naye naam 'odoo16.*' se; standard templates ko haath nahi lagta)")
    V, R = "ir.ui.view", "ir.actions.report"
    keys = []
    for m in a.models:
        keys += CLONE_SETS.get(m, [])
    src_views = {}
    for k in keys:
        f = S.call(V, "search_read", [("key", "=", k)], fields=["name", "key", "arch_db"], context=CTX)
        if f:
            src_views[k] = f[0]
        else:
            row("clone", "", k, "SKIP", "source mein template nahi")
    created = {}
    for k, v in src_views.items():
        arch = v.get("arch_db") or ""
        for rule, rep in REWRITES:
            arch = re.sub(rule, rep, arch)
        for old in src_views:
            for q in ('"', "'"):
                arch = arch.replace("t-call=%s%s%s" % (q, old, q), "t-call=%s%s%s" % (q, newkey(old), q))
                arch = arch.replace("t-name=%s%s%s" % (q, old, q), "t-name=%s%s%s" % (q, newkey(old), q))
        targets = sorted(set(re.findall(r"t-call=[\"']([^\"']+)[\"']", arch)))
        missing = [t for t in targets if not t.startswith("odoo16.") and not dst_view_by_key(t)]
        nk = newkey(k)
        note = ("dst mein ye templates nahi (print par error aa sakta hai): " + ", ".join(missing)) if missing else ""
        if dst_view_by_key(nk):
            row("clone-view", v["id"], nk, "EXISTS", note)
            continue
        if not a.apply:
            row("clone-view", v["id"], nk, "WILL_ADD", note)
            continue
        try:
            did = D.call(V, "create", {"name": v["name"] + " (Odoo 16)", "type": "qweb", "key": nk, "arch_db": arch,
                                       "mode": "primary", "active": True}, context=CTX)
            created[k] = did
            row("clone-view", v["id"], nk, "CREATED", ("dst #%s | " % did) + note)
        except Exception as e:
            row("clone-view", v["id"], nk, "ERROR", str(e)[:300])
    # report actions
    fl = [f for f in ("name", "model", "report_name", "report_file", "print_report_name", "paperformat_id")
          if f in S.fields(R)]
    for r in S.call(R, "search_read", [("model", "in", a.models), ("report_name", "in", list(src_views))],
                    fields=fl, context=CTX):
        nk = newkey(r["report_name"])
        if D.call(R, "search", [("report_name", "=", nk)], context=CTX):
            row("clone-report", r["id"], nk, "EXISTS", "")
            continue
        if not a.apply:
            row("clone-report", r["id"], nk, "WILL_ADD", r["name"] + " (Odoo 16)")
            continue
        try:
            vals = {"name": r["name"] + " (Odoo 16)", "model": r["model"], "report_type": "qweb-pdf",
                    "report_name": nk, "report_file": newkey(r.get("report_file") or r["report_name"])}
            if r.get("print_report_name"):
                vals["print_report_name"] = r["print_report_name"]
            if r.get("paperformat_id"):
                pf = dst_paperformat_for(m2o_id(r["paperformat_id"]))
                if pf:
                    vals["paperformat_id"] = pf
            mid = D.call("ir.model", "search", [("model", "=", r["model"])], context=CTX)
            if mid:
                vals["binding_model_id"] = mid[0]
                vals["binding_type"] = "report"
            did = D.call(R, "create", vals, context=CTX)
            row("clone-report", r["id"], nk, "CREATED", "dst #%s - Print menu mein '%s (Odoo 16)' dikhega" % (did, r["name"]))
        except Exception as e:
            row("clone-report", r["id"], nk, "ERROR", str(e)[:300])


def main():
    global S, D
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["list", "run", "clone"])
    ap.add_argument("--layout", default="", help="company ki document layout ka dst key (jaise web.external_layout_standard)")
    ap.add_argument("--apply", action="store_true", help="asal mein likho (warna dry-run)")
    ap.add_argument("--inactive", action="store_true", help="naye custom templates inactive bana kar create karo")
    ap.add_argument("--models", default="sale.order,account.move")
    ap.add_argument("--roots", default="", help="extra template key prefixes, comma separated")
    ap.add_argument("--only", default="paperformats,companies,reports,views")
    a = ap.parse_args()
    a.models = [x.strip() for x in a.models.split(",") if x.strip()]
    a.roots = [x.strip() for x in a.roots.split(",") if x.strip()]
    if a.cmd == "list":
        a.apply = False
    S, D = Source(), Dest()
    print("%s | models: %s" % ("APPLY" if a.apply else "DRY-RUN (dst mein kuch nahi likha ja raha)", ", ".join(a.models)))
    steps = [s.strip() for s in a.only.split(",")]
    try:
        if a.cmd == "clone":
            clone(a)
        else:
            for name, fn in (("paperformats", do_paperformats), ("companies", do_companies), ("reports", do_reports),
                             ("views", do_views)):
                if name in steps:
                    fn(a)
    finally:
        with open("reports_migration.csv", "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["kind", "src_id", "name_or_key", "action", "detail"])
            w.writerows(ROWS)
        print("\nSummary: %s\nReport: reports_migration.csv" % dict(collections.Counter(r[3] for r in ROWS)))
        if not a.apply:
            print("Theek lage to: python3 migrate_reports.py %s --apply   (pehle backup)" % ("clone" if a.cmd == "clone" else "run"))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit("Stopped.")
    except Exception as exc:
        sys.exit("Reports migration stopped: %s" % exc)
