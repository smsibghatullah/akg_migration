#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
migrate_transactions.py ka extension (usi folder mein rakho). Original file ko chhedna nahi parta.

Kya add karta hai:
  1. check --fix mein product.product (variants) ka mapping: product.template ke map se
  2. payroll masters ka auto-mapping (salary structure / rules / categories / work entry types)
  3. naye steps: expense_sheets, expenses, approvals, payslips   (core mein shamil)

Chalana bilkul pehle jaisa, bas file ka naam badal do:
  python3 migrate_transactions_ext.py list
  python3 migrate_transactions_ext.py check --fix
  python3 migrate_transactions_ext.py run --steps core --limit 5 --dry-run
  python3 migrate_transactions_ext.py run --steps core --workers 4
  python3 migrate_transactions_ext.py run --steps payslips --limit 5
"""
import sys
import collections

import migrate_transactions as mt

# ---------------------------------------------------------------- 1. extra master keys
mt.KEYS.update({
    "hr.work.entry.type": [("code",)],
    "hr.payroll.structure.type": [("name",)],
    "hr.payroll.structure": [("code",), ("name",)],
    "hr.salary.rule.category": [("code",)],
    "hr.salary.rule": [("code", "struct_id")],
    "approval.category": [("name",)],
})
mt.DEPS[:] = list(mt.KEYS)


# ---------------------------------------------------------------- 2. product.product mapping
def fix_variants():
    pm = "product.product"
    if mt.src.fields(pm) is None or mt.dst.fields(pm) is None:
        return 0
    ctxa = {"active_test": False}
    done = mt.MAP.done_src_ids(pm)
    by_t = collections.defaultdict(list)
    for r in mt.src.call(pm, "search_read", [], fields=["product_tmpl_id", "default_code"], context=ctxa):
        if r["id"] in done:
            continue
        t = mt.resolve("product.template", mt.m2o_id(r["product_tmpl_id"]))
        if t:
            by_t[t].append(r)
    claimed = mt.MAP.dst_ids(pm)
    tids, n = list(by_t), 0
    for j in range(0, len(tids), 500):
        chunk = tids[j:j + 500]
        dby = collections.defaultdict(list)
        for d in mt.dst.call(pm, "search_read", [("product_tmpl_id", "in", chunk)],
                             fields=["product_tmpl_id", "default_code"], order="id", context=ctxa):
            if d["id"] not in claimed:
                dby[mt.m2o_id(d["product_tmpl_id"])].append(d)
        for t in chunk:
            ss = sorted(by_t[t], key=lambda x: x["id"])
            dd = dby.get(t, [])
            if len(ss) == 1 and len(dd) == 1:
                pairs = [(ss[0], dd[0])]
            else:   # multi-variant: sirf default_code se match
                dc = {d["default_code"]: d for d in dd if d.get("default_code")}
                pairs = [(s, dc[s["default_code"]]) for s in ss if s.get("default_code") in dc]
            if not pairs:
                mt.log.warning("variant map nahi bana: dst template %s (src variants %d, dst %d)", t, len(ss), len(dd))
                continue
            for s, d in pairs:
                mt.MAP.set_master(pm, s["id"], d["id"])
                claimed.add(d["id"])
                n += 1
    return n


_orig_check = mt.cmd_check


def check_with_variants(a):
    _orig_check(a)
    if a.fix:
        n = fix_variants()
        mt.MAP.save_master()
        print("product.product (variants) map hue: %d  -> ab dobara `check` chalao" % n)


mt.cmd_check = check_with_variants


# ---------------------------------------------------------------- 3. payslip: Odoo 20 mein contract ki jagah version
def pre_payslip(vals, rec, unresolved):
    df = mt.dst.fields("hr.payslip") or {}
    emp = vals.get("employee_id")
    if "version_id" in df and emp and not vals.get("version_id"):
        try:
            base = [("employee_id", "=", emp)]
            dom = base + ([("date_version", "<=", rec["date_to"])] if rec.get("date_to") else [])
            for d in (dom, base):
                r = mt.dst.call("hr.version", "search_read", d, fields=["id"], order="date_version desc, id desc",
                                limit=1, context={"active_test": False})
                if r:
                    vals["version_id"] = r[0]["id"]
                    return
        except Exception as e:
            mt.log.warning("hr.version lookup fail: %s", str(e)[:150])
        unresolved.append(("version_id", "hr.version", 0))


_orig_convert = mt.convert


def convert_with_pre(rec, plan, dfields, strict, label):
    vals, un = _orig_convert(rec, plan, dfields, strict, label)
    if label == "hr.payslip":
        pre_payslip(vals, rec, un)
    return vals, un


mt.convert = convert_with_pre

# ---------------------------------------------------------------- 3b. renamed fields (Odoo 16 -> 20)
# Original script sirf same-naam wali fields copy karti hai. Odoo 19+ mein kuch fields ka naam badal gaya
# (jaise sale line ka tax_id -> tax_ids), is liye taxes copy nahi hote the aur Odoo product ke default tax laga deta tha.
ALIASES = {
    ("sale.order.line", "tax_id"): ["tax_ids"],
    ("sale.order.line", "product_uom"): ["product_uom_id"],
    ("purchase.order.line", "taxes_id"): ["tax_ids"],
    ("purchase.order.line", "product_uom"): ["product_uom_id"],
}
_orig_build_plan = mt.build_plan


def build_plan_with_aliases(smodel, dmodel, skip=()):
    plan = _orig_build_plan(smodel, dmodel, skip)
    sf, df = mt.src.fields(smodel), mt.dst.fields(dmodel)
    for (m, f), cands in ALIASES.items():
        if m == smodel and f in sf and f not in plan and f not in skip:
            for c in cands:
                if c in df and mt.compatible(sf[f]["type"], df[c]["type"]):
                    plan[f] = (c, sf[f]["type"], df[c]["type"], sf[f].get("relation"))
                    break
    return plan


mt.build_plan = build_plan_with_aliases
# alias wali nayi field names bhi "strict" honi chahiye (tax/uom na mile to document skip ho, galat tax na lage)
mt.SPECS["sales"]["lines"]["strict"] += ["tax_ids", "product_uom_id"]
mt.SPECS["purchases"]["lines"]["strict"] += ["tax_ids", "product_uom_id"]

# ---------------------------------------------------------------- 4. naye steps
mt.SPECS.update({
    "expense_sheets": dict(
        model="hr.expense.sheet", date=("create_date", True), strict=["employee_id"],
        lines=dict(model="hr.expense", fk="sheet_id", o2m="expense_line_ids", strict=["employee_id", "product_id"]),
        state=mt.state_generic_write("hr.expense.sheet"), map_lines=True,
        desc="Expense reports + expense lines (Odoo 20 mein model na ho to SKIP)"),
    "expenses": dict(
        model="hr.expense", date=("date", False), strict=["employee_id"],
        desc="Expenses jo kisi report (sheet) mein nahi / ya Odoo 20 mein sheet nahi"),
    "approvals": dict(
        model="approval.request", date=("create_date", True), strict=["category_id"],
        lines=dict(model="approval.approver", fk="request_id", o2m="approver_ids", strict=["user_id"]),
        map_lines=True, desc="Approval requests + approvers"),
    "payslips": dict(
        model="hr.payslip", date=("date_from", False), strict=["employee_id", "struct_id"],
        lines=dict(model="hr.payslip.line", fk="slip_id", o2m="line_ids", strict=["salary_rule_id"]),
        state=mt.state_generic_write("hr.payslip"), map_lines=True,
        desc="Payslips + payslip lines (EXPERIMENTAL - pehle --limit 5)"),
})
mt.CORE.extend(["expense_sheets", "expenses", "approvals", "payslips"])


def cmd_gaps():
    """python3 migrate_transactions_ext.py gaps  -> wo source fields jo Odoo 20 mein hain hi nahi (rename/remove)"""
    mt.src, mt.dst = mt.Src(), mt.Dst()
    for name, spec in mt.SPECS.items():
        for m in [x for x in (spec.get("model"), (spec.get("lines") or {}).get("model")) if x]:
            sf, df = mt.src.fields(m), mt.dst.fields(m)
            if sf is None or df is None:
                print("%-32s model nahi (%s)" % (m, "source" if sf is None else "destination"))
                continue
            plan = mt.build_plan(m, m)
            miss = sorted(f for f, s in sf.items()
                          if f not in plan and f not in df and s.get("store") and s["type"] not in ("one2many", "binary")
                          and f not in mt.SKIP_FIELDS and not f.startswith(("message_", "activity_", "website_message")))
            print("%-32s dst mein nahi: %s" % (m, ", ".join(miss) or "-"))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "gaps":
        cmd_gaps()
        sys.exit(0)
    try:
        mt.main()
    except KeyboardInterrupt:
        if mt.MAP:
            mt.MAP.commit()
        sys.exit("Stopped by user (map save ho chuka).")
    except Exception as exc:
        mt.log.exception("Migration stopped")
        sys.exit(str(exc))