#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Odoo 16 ki hr.leave.type  ->  Odoo 20 ki hr.work.entry.type (time off type) banata hai
aur id-map mein ("hr.leave.type", src_id) -> dst_id likhta hai, taake allocations / leaves
ka holiday_status_id resolve ho sake.

* Naam se match NAHI karta (target mein 3-3 country wale duplicates hain), har type ke liye
  apna naya record banata hai, code = OMLT<src_id>. Dobara chalane par duplicate nahi banata.
* Naya record target ke maujooda time-off type ki COPY se banta hai (taake NOT NULL columns
  aur selection values sahi hon), phir source ki values upar se lagti hain.

Chalana (migrate_transactions.py ke folder mein):
  python3 migrate_leave_types.py              # sirf dikhata hai (dry-run)
  python3 migrate_leave_types.py --apply      # asal mein banata hai
  python3 migrate_leave_types.py --apply --template 643
Shart: koi aur migrate_* process na chal raha ho (txmap.sqlite3 lock hoti hai).
"""
import argparse
import sys

import migrate_transactions as mt

SRC_MODEL = "hr.leave.type"
DST_MODEL = "hr.work.entry.type"
NOTSET = object()
CTX = getattr(mt, "CTX", {}) or {}

COPY_FIELDS = ["active", "requires_allocation", "employee_requests", "leave_validation_type",
               "allocation_validation_type", "request_unit", "unpaid", "support_document",
               "include_public_holidays_in_duration", "create_calendar_meeting",
               "hide_on_dashboard"]


def conv(val, dfld):
    t = dfld.get("type")
    if t == "boolean":
        if isinstance(val, str):
            return val.lower() in ("yes", "true", "1")
        return bool(val)
    if t == "selection":
        allowed = [x[0] for x in (dfld.get("selection") or [])]
        return val if val in allowed else NOTSET
    return val


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--template", type=int, default=643,
                    help="target ka koi maujooda time-off type jiski copy banni hai (default 643)")
    a = ap.parse_args()

    mt.src, mt.dst, mt.MAP = mt.Src(), mt.Dst(), mt.IdMap()
    sfields = mt.src.fields(SRC_MODEL) or {}
    dfields = mt.dst.fields(DST_MODEL) or {}
    ctx_all = dict(CTX, active_test=False)

    want = [f for f in ["name"] + COPY_FIELDS if f in sfields]
    rows = mt.src.call(SRC_MODEL, "search_read", [], fields=want, context=ctx_all)
    rows.sort(key=lambda r: r["id"])
    print("Source leave types: %d" % len(rows))

    tmpl = mt.dst.call(DST_MODEL, "read", [a.template], fields=["name", "code", "time_off_selectable"], context=CTX)
    if not tmpl:
        sys.exit("Template id %s target mein nahi mila (--template se dusra id dein)" % a.template)
    print("Template: id %s  %s  (%s)" % (a.template, tmpl[0].get("name"), tmpl[0].get("code")))

    made = 0
    for r in rows:
        sid, name = r["id"], r["name"]
        code = "OMLT%d" % sid
        done = mt.MAP.get(SRC_MODEL, sid)
        if done:
            print("  src %-3s %-40s -> pehle se map: dst %s" % (sid, name, done))
            continue
        ex = mt.dst.call(DST_MODEL, "search_read", [("code", "=", code)], fields=["id"], context=ctx_all)
        if ex:
            did = ex[0]["id"]
            print("  src %-3s %-40s -> maujood record mila: dst %s (map mein likh raha)" % (sid, name, did))
            if a.apply:
                mt.MAP.set(SRC_MODEL, sid, did)
            continue

        vals = {"name": name, "code": code, "country_id": False}
        if "time_off_selectable" in dfields:
            vals["time_off_selectable"] = True
        for f in COPY_FIELDS:
            if f in r and f in dfields:
                v = conv(r[f], dfields[f])
                if v is not NOTSET:
                    vals[f] = v
        if "unit_of_measure" in dfields and r.get("request_unit"):
            v = conv("hour" if r["request_unit"] == "hour" else "day", dfields["unit_of_measure"])
            if v is not NOTSET:
                vals["unit_of_measure"] = v

        print("  src %-3s %-40s -> banega code=%s %s" % (sid, name, code,
              {k: vals[k] for k in vals if k not in ("name", "code", "country_id")}))
        if not a.apply:
            continue

        try:
            res = mt.dst.call(DST_MODEL, "copy", ids=[a.template], default=vals, context=CTX)
            did = res[0] if isinstance(res, list) else res
        except Exception as e:
            print("     full copy fail (%s) - sirf naam/code se copy karke fields ek ek likh raha" % str(e)[:150])
            res = mt.dst.call(DST_MODEL, "copy", ids=[a.template],
                              default={"name": name, "code": code, "country_id": False}, context=CTX)
            did = res[0] if isinstance(res, list) else res
            for k, v in vals.items():
                if k in ("name", "code", "country_id"):
                    continue
                try:
                    mt.dst.call(DST_MODEL, "write", [did], {k: v}, context=CTX)
                except Exception as e2:
                    print("       %s=%r nahi laga: %s" % (k, v, str(e2)[:100]))
        mt.MAP.set(SRC_MODEL, sid, did)
        made += 1
        print("     bana: dst %s" % did)

    if a.apply:
        mt.MAP.db.commit()
        print("Map save ho gaya. Naye types: %d" % made)
    else:
        print("\nYeh sirf dry-run tha. Asal mein banane ke liye --apply lagayein.")


if __name__ == "__main__":
    main()
