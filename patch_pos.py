#!/usr/bin/env python3
import shutil
P = "migrate_pos.py"
shutil.copy(P, P + ".bak")
s = open(P, encoding="utf-8").read()


def rep(old, new):
    global s
    assert s.count(old) == 1, "anchor nahi mila / ek se zyada:\n" + old[:80]
    s = s.replace(old, new)


# 1) grouping destination config se + source payment method ke naam
rep('''    groups = collections.defaultdict(list)
    for sid in sids:
        groups[mt.m2o_id(ctxd["srecs"][sid].get("config_id"))].append(sid)
''', '''    ctxd["pm_name"] = {r["id"]: r["name"] for r in mt.src.call(
        "pos.payment.method", "search_read", [], fields=["name"], context={"active_test": False})}
    groups = collections.defaultdict(list)
    for sid in sids:
        scfg = mt.m2o_id(ctxd["srecs"][sid].get("config_id"))
        dcfg = mt.MAP.get("pos.config", scfg) if scfg else None
        groups[("dst", dcfg) if dcfg else ("unmapped", scfg)].append(sid)
''')

# 2) helper: destination config ke allowed payment methods {naam: id}
rep('''def migrate_one_order(rec, lines, pays, dsid, ctxd, a):''', '''_pmc, _pmlock = {}, threading.Lock()


def cfg_methods(dcfg):
    with _pmlock:
        if dcfg not in _pmc:
            ids = mt.dst.call("pos.config", "read", [dcfg], fields=["payment_method_ids"],
                              context=CTX)[0]["payment_method_ids"]
            rows = mt.dst.call("pos.payment.method", "read", ids, fields=["name"],
                               context={"active_test": False}) if ids else []
            _pmc[dcfg] = {r["name"]: r["id"] for r in rows}
        return _pmc[dcfg]


def migrate_one_order(rec, lines, pays, dsid, ctxd, a):''')

# 3) payment method: usi config ka sahi wala
rep('''        pv, un = mt.convert(pr, ctxd["pay_plan"], ctxd["pdf"], PAY_STRICT, PAY)
        unresolved += un
''', '''        pv, un = mt.convert(pr, ctxd["pay_plan"], ctxd["pdf"], PAY_STRICT, PAY)
        scfg = mt.m2o_id(rec.get("config_id"))
        dcfg = mt.MAP.get("pos.config", scfg) if scfg else None
        pmn = ctxd["pm_name"].get(mt.m2o_id(pr.get("payment_method_id")))
        alt = cfg_methods(dcfg).get(pmn) if (dcfg and pmn) else None
        if alt:
            pv["payment_method_id"] = alt
            un = [u for u in un if u[1] != "pos.payment.method"]
        unresolved += un
''')

# 4) order draft banao, state / invoice baad mein write
rep('''    did = mt.dst.call(ORD, "create", vals, context=CTX)
    mt.MAP.set(ORD, oid, did)
    notes = []
    st = rec.get("state")
''', '''    am = vals.pop("account_move", None)
    vals.pop("state", None)
    did = mt.dst.call(ORD, "create", vals, context=CTX)
    mt.MAP.set(ORD, oid, did)
    notes = []
    if am:
        try:
            mt.dst.call(ORD, "write", [did], {"account_move": am}, context=CTX)
        except Exception as e:
            notes.append("WARN account_move: " + str(e)[:200])
    st = rec.get("state")
''')

open(P, "w", encoding="utf-8").write(s)
print("patch lag gaya (backup: %s.bak)" % P)
