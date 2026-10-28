#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
"Another entry with the same name already exists" ka masla.

  python3 fix_move_states.py diagnose 41352     # 41352 = Odoo 16 ka account.move id jo fail hua
  python3 fix_move_states.py repost [--limit N] # jo moves Odoo 20 mein draft reh gaye, unka state dobara lagao

Pehle diagnose chalao, wajah theek karo, phir repost.
"""
import sys
import collections
import migrate_transactions as mt


def init():
    mt.src, mt.dst, mt.MAP = mt.Src(), mt.Dst(), mt.IdMap()


def diagnose(sid):
    did = mt.MAP.get("account.move", sid)
    f = ["name", "journal_id", "company_id", "state", "date", "move_type", "ref"]
    s = mt.src.call("account.move", "read", [sid], fields=f)[0]
    print("SRC #%s: name=%s | journal=%s | company=%s | state=%s | date=%s | type=%s" % (
        sid, s["name"], s["journal_id"], s["company_id"], s["state"], s["date"], s["move_type"]))
    if not did:
        print("is move ka dst mein map nahi hai")
        return
    d = mt.dst.call("account.move", "read", [did], fields=f, context=mt.CTX)[0]
    print("DST #%s: name=%s | journal=%s | company=%s | state=%s | date=%s" % (
        did, d["name"], d["journal_id"], d["company_id"], d["state"], d["date"]))
    dj = mt.m2o_id(d["journal_id"])
    others = mt.dst.call("account.move", "search_read",
                         [("name", "=", d["name"]), ("journal_id", "=", dj), ("id", "!=", did)],
                         fields=["name", "state", "date", "ref", "move_type", "create_date", "company_id"],
                         context=mt.CTX)
    print("\nDST mein usi journal mein isi naam ki dusri entries: %d" % len(others))
    for o in others:
        row = mt.MAP.db.execute("select src from m where model='account.move' and dst=?", (o["id"],)).fetchone()
        origin = ("is migration se bani (src#%s)" % row[0]) if row else "PEHLE SE DST MEIN THI (is script ne nahi banayi)"
        print("  dst#%s state=%s date=%s type=%s ref=%s created=%s -> %s" % (
            o["id"], o["state"], o["date"], o["move_type"], o.get("ref"), o.get("create_date"), origin))
    # kaun kaun se src journals is dst journal par map hain
    sj_ids = mt.src.call("account.journal", "search", [], context={"active_test": False})
    same = [j for j in sj_ids if mt.resolve("account.journal", j) == dj]
    names = {r["id"]: r["name"] for r in mt.src.call("account.journal", "read", same, fields=["name"])} if same else {}
    print("\nIs dst journal #%s par map hone wale source journals: %s" % (dj, names or "-"))
    if len(same) > 1:
        print("  -> KAI source journals ek hi dst journal par map hain. Isi wajah se naam takra rahe hain.")


def repost(limit, rename=False):
    pairs = mt.MAP.tx_src_ids("account.move")
    fixed, still, other = 0, collections.Counter(), 0
    todo = 0
    for j in range(0, len(pairs), 500):
        chunk = pairs[j:j + 500]
        sstate = {r["id"]: r["state"] for r in mt.src.call("account.move", "read", [p[0] for p in chunk],
                                                           fields=["state"], context=mt.CTX)}
        dstate = {r["id"]: r["state"] for r in mt.dst.call("account.move", "read", [p[1] for p in chunk],
                                                           fields=["state"], context=mt.CTX)}
        for sid, did in chunk:
            s, d = sstate.get(sid), dstate.get(did)
            if not s or s == "draft" or d != "draft":
                continue
            if limit and todo >= limit:
                break
            todo += 1
            try:
                mt.state_move(did, {"state": s})
                fixed += 1
            except Exception as e:
                if rename and "same name" in str(e):
                    try:
                        if rename_move(sid, did):
                            mt.state_move(did, {"state": s})
                            fixed += 1
                            continue
                    except Exception as e2:
                        e = e2
                still[str(e)[:110]] += 1
        if limit and todo >= limit:
            break
    print("state theek hui: %d" % fixed)
    if RENAMED:
        import csv
        with open("renamed_moves.csv", "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["src_id", "dst_id", "old_name", "new_name"])
            w.writerows(RENAMED)
        print("naam badle gaye: %d (list: renamed_moves.csv)" % len(RENAMED))
    for msg, c in still.most_common(5):
        print("  abhi bhi fail (%d): %s" % (c, msg))


RENAMED = []


def rename_move(sid, did):
    """Draft move ka naam ek unique naam se badalta hai (original naam + '-DUP'). True agar badal gaya."""
    d = mt.dst.call("account.move", "read", [did], fields=["name", "journal_id", "company_id"], context=mt.CTX)[0]
    for i in range(1, 50):
        cand = "%s-DUP%s" % (d["name"], "" if i == 1 else i)
        if not mt.dst.call("account.move", "search", [("name", "=", cand), ("journal_id", "=", mt.m2o_id(d["journal_id"]))],
                           context=mt.CTX):
            mt.dst.call("account.move", "write", [did], {"name": cand}, context=mt.CTX_MOVE)
            RENAMED.append((sid, did, d["name"], cand))
            return True
    return False


def dupnames():
    """Read-only: SOURCE mein posted moves jin ka naam (journal + company mein) ek se zyada baar hai."""
    import csv
    cnt, ids, off = collections.Counter(), collections.defaultdict(list), 0
    while True:
        part = mt.src.call("account.move", "search_read", [("state", "=", "posted")],
                           fields=["name", "journal_id", "company_id", "move_type", "date"], limit=5000, offset=off,
                           order="id", context=mt.CTX)
        for r in part:
            k = (r["name"], mt.m2o_id(r["journal_id"]), mt.m2o_id(r["company_id"]))
            cnt[k] += 1
            ids[k].append((r["id"], r["move_type"], r["date"]))
        if len(part) < 5000:
            break
        off += 5000
    dups = {k: v for k, v in ids.items() if cnt[k] > 1}
    print("posted moves: %d | duplicate naam wale groups: %d | un mein moves: %d" % (
        sum(cnt.values()), len(dups), sum(len(v) for v in dups.values())))
    for k, v in list(dups.items())[:20]:
        print("  %s (journal %s): %s" % (k[0], k[1], v))
    with open("dup_names_source.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["name", "journal_id", "company_id", "src_id", "move_type", "date"])
        for k, v in dups.items():
            for i, t, dt in v:
                w.writerow([k[0], k[1], k[2], i, t, dt])
    print("poori list: dup_names_source.csv")


def names():
    """Read-only: migrate hue moves jin ka naam Odoo 20 mein source se alag ho gaya. names_mismatch.csv banata hai."""
    import csv
    pairs = mt.MAP.tx_src_ids("account.move")
    bad = []
    for j in range(0, len(pairs), 500):
        chunk = pairs[j:j + 500]
        sn = {r["id"]: r["name"] for r in mt.src.call("account.move", "read", [p[0] for p in chunk],
                                                     fields=["name"], context=mt.CTX)}
        dn = {r["id"]: r for r in mt.dst.call("account.move", "read", [p[1] for p in chunk],
                                             fields=["name", "state"], context=mt.CTX)}
        for sid, did in chunk:
            if did in dn and sn.get(sid) is not None and sn[sid] != dn[did]["name"]:
                bad.append((sid, did, sn[sid], dn[did]["name"], dn[did]["state"]))
    print("migrate hue moves: %d | naam alag: %d" % (len(pairs), len(bad)))
    for b in bad[:30]:
        print("  src#%s dst#%s  src naam=%s  dst naam=%s  dst state=%s" % b)
    with open("names_mismatch.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["src_id", "dst_id", "src_name", "dst_name", "dst_state"])
        w.writerows(bad)
    print("poori list: names_mismatch.csv")


def adopt(apply, limit):
    """Jo moves draft reh gaye aur jin ki asli copy pehle se dst mein hai (map kho gaya tha):
    asli copy ko map mein wapas jodta hai, extra draft copy hata deta hai. Default: sirf report (--apply se karta hai)."""
    pairs = mt.MAP.tx_src_ids("account.move")
    cand = []
    for j in range(0, len(pairs), 500):
        chunk = pairs[j:j + 500]
        ss = {r["id"]: r["state"] for r in mt.src.call("account.move", "read", [p[0] for p in chunk],
                                                      fields=["state"], context=mt.CTX)}
        ds = {r["id"]: r["state"] for r in mt.dst.call("account.move", "read", [p[1] for p in chunk],
                                                      fields=["state"], context=mt.CTX)}
        cand += [(sid, did, ss[sid]) for sid, did in chunk if ss.get(sid) not in (None, "draft") and ds.get(did) == "draft"]
    if limit:
        cand = cand[:limit]
    print("draft reh gaye moves (jo source mein draft nahi): %d" % len(cand))
    ok, skipped = 0, collections.Counter()
    for sid, did, sstate in cand:
        d = mt.dst.call("account.move", "read", [did], fields=["name", "journal_id", "company_id"], context=mt.CTX)[0]
        others = mt.dst.call("account.move", "search_read",
                             [("name", "=", d["name"]), ("journal_id", "=", mt.m2o_id(d["journal_id"])),
                              ("company_id", "=", mt.m2o_id(d["company_id"])), ("id", "!=", did)],
                             fields=["state", "amount_total"], context=mt.CTX)
        if len(others) != 1:
            skipped["takraane wali entries %d (1 chahiye)" % len(others)] += 1
            print("  skip src#%s (%s): takraane wali entries %d" % (sid, d["name"], len(others)))
            continue
        o = others[0]
        if mt.MAP.db.execute("select 1 from m where model='account.move' and dst=?", (o["id"],)).fetchone():
            skipped["dusri entry pehle se map hai"] += 1
            print("  skip src#%s (%s): dst#%s kisi aur source move se map hai" % (sid, d["name"], o["id"]))
            continue
        s = mt.src.call("account.move", "read", [sid], fields=["amount_total"], context=mt.CTX)[0]
        sl = mt.src.call("account.move.line", "search", [("move_id", "=", sid)], order="id", context=mt.CTX)
        dl = mt.dst.call("account.move.line", "search", [("move_id", "=", o["id"])], order="id", context=mt.CTX)
        if o["state"] != sstate or len(sl) != len(dl) or abs((s["amount_total"] or 0) - (o["amount_total"] or 0)) > 0.011:
            skipped["asli copy se mel nahi khata"] += 1
            print("  skip src#%s (%s): dst#%s state=%s lines %d/%d amount %s/%s" % (
                sid, d["name"], o["id"], o["state"], len(sl), len(dl), s["amount_total"], o["amount_total"]))
            continue
        if apply:
            mt.dst.call("account.move", "unlink", [did], context=dict(mt.CTX_MOVE, force_delete=True))
            for s_, d_ in zip(sl, dl):
                mt.MAP.set("account.move.line", s_, d_)
            mt.MAP.set("account.move", sid, o["id"])
            mt.MAP.commit()
        ok += 1
        print("  %s src#%s (%s): dst#%s asli copy%s" % ("OK" if apply else "ho jayega", sid, d["name"], o["id"],
                                                         ", draft #%s hata di" % did if apply else ""))
    print("\n%s: %d | skip: %s" % ("adopt hue" if apply else "adopt ho sakte hain", ok, dict(skipped) or "-"))
    if not apply and ok:
        print("Karne ke liye: python3 fix_move_states.py adopt --apply")


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("diagnose", "repost", "adopt", "names", "dupnames"):
        sys.exit(__doc__)
    init()
    if sys.argv[1] == "diagnose":
        diagnose(int(sys.argv[2]))
    elif sys.argv[1] == "names":
        names()
    elif sys.argv[1] == "dupnames":
        dupnames()
    elif sys.argv[1] == "adopt":
        lim = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else 0
        adopt("--apply" in sys.argv, lim)
    else:
        lim = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else 0
        repost(lim, "--rename-dups" in sys.argv)
        mt.MAP.commit()


if __name__ == "__main__":
    main()
