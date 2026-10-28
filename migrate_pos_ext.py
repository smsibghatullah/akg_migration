#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
migrate_pos.py ka extension (usi folder mein rakho). Original file ko chhedna nahi parta.

Masla:  "HTTP 422 pos.order/create: The payment method selected is not allowed in the config of the POS session."
Wajah:  Odoo 20 har payment ke liye check karta hai ke uska payment method session ki POS config mein allowed ho.
        Odoo 16 mein purane orders ke methods baad mein config se hat chuke hote hain, aur Odoo 20 mein aik CASH method
        sirf aik POS config mein ho sakta hai.
Hal:    Har session se pehle us session ki payments ke methods dekhta hai:
          * normal (non-cash) method config mein nahi ho to config mein jod deta hai
          * cash method kisi aur POS mein laga ho to us config ke liye uski COPY banata hai
            ("<method> (<POS config>)", wahi journal) aur us session ki payments usi copy par laga deta hai.

Chalana (migrate_pos.py jaisa hi, bas naam badal do):
  python3 migrate_pos_ext.py run --limit 3
  python3 migrate_pos_ext.py run --workers 4
  python3 migrate_pos_ext.py verify
  python3 migrate_pos_ext.py fix-open-sessions      # Ctrl+C ke baad 'opened' reh gayi sessions band karo
  python3 migrate_pos_ext.py cleanup-clones [--delete]   # faltu cash-method copies (pehle sirf list)

Odoo 20 niyam: ek POS config mein sirf EK cash method, aur ek cash method sirf ek config mein. Isliye:
  * config ka apna cash method ho to purani payments (kisi bhi cash method ki) usi par lagti hain
  * apna cash method na ho aur method kisi aur POS mein ho to copy banti hai
"""
import sys
import threading

import migrate_pos as mp

mt = mp.mt
log = mt.log
CTX = mt.CTX
_tl = threading.local()
_lock = threading.Lock()
_clone_cache = {}


def session_methods(sid):
    """Source session ki payments mein jo payment methods use hue"""
    pids = mt.src.call(mp.PAY, "search", [("session_id", "=", sid)], context=CTX)
    out = set()
    for j in range(0, len(pids), 500):
        for r in mt.src.call(mp.PAY, "read", pids[j:j + 500], fields=["payment_method_id"], context=CTX):
            if r.get("payment_method_id"):
                out.add(mt.m2o_id(r["payment_method_id"]))
    return out


_cfg_state = {}      # cfg -> {"have": set(ids), "cash": id ya None}   (har config aik hi worker se chalti hai)


def _cfg_info(cfg, fl):
    st = _cfg_state.get(cfg)
    if st is None:
        cur = mt.dst.call("pos.config", "read", [cfg], fields=["name", "payment_method_ids"], context=CTX)[0]
        have = set(cur["payment_method_ids"])
        cash = None
        if have:
            for r in mt.dst.call("pos.payment.method", "read", sorted(have), fields=[f for f in fl if f != "config_ids"], context=CTX):
                if (r.get("type") == "cash") if "type" in r else bool(r.get("is_cash_count")):
                    cash = r["id"]
                    break
        st = _cfg_state[cfg] = {"have": have, "cash": cash, "name": cur["name"]}
    return st


def ensure_methods(cfg, needed_src):
    """dst config `cfg` mein zaroori methods maujood karo. Return {dst_method: istemal hone wala method}.
    Odoo 20 niyam: config mein SIRF EK cash method, aur aik cash method sirf aik config mein."""
    pmf = mt.dst.fields("pos.payment.method") or {}
    fl = [f for f in ("name", "type", "is_cash_count", "config_ids") if f in pmf]
    st = _cfg_info(cfg, fl)
    remap, add = {}, []
    for smid in needed_src:
        dm = mt.resolve("pos.payment.method", smid)
        if not dm or dm in st["have"]:
            continue
        info = mt.dst.call("pos.payment.method", "read", [dm], fields=fl, context=CTX)[0]
        is_cash = (info.get("type") == "cash") if "type" in info else bool(info.get("is_cash_count"))
        if is_cash:
            if st["cash"]:                       # is POS ka apna cash method pehle se hai -> payments usi par
                remap[dm] = st["cash"]
                continue
            others = [c for c in info.get("config_ids", []) if c != cfg]
            if others:                           # kisi aur POS ka cash method -> is POS ke liye copy
                key = (dm, cfg)
                with _lock:
                    clone = _clone_cache.get(key)
                    if not clone:
                        res = mt.dst.call("pos.payment.method", "copy", ids=[dm],
                                          default={"name": "%s (%s)" % (info["name"], st["name"])}, context=CTX)
                        clone = res[0] if isinstance(res, list) else res
                        _clone_cache[key] = clone
                        log.warning("cash method '%s' kisi aur POS mein hai aur '%s' ka apna cash nahi - copy (id %s) banayi",
                                    info["name"], st["name"], clone)
                remap[dm] = clone
                add.append(clone)
                st["cash"] = clone
            else:
                add.append(dm)
                st["cash"] = dm
        else:
            add.append(dm)
    if add:
        mt.dst.call("pos.config", "write", [cfg], {"payment_method_ids": [[4, x] for x in add]}, context=CTX)
        st["have"].update(add)
    return remap


_orig_session = mp.process_session


def process_session_ensure(sid, a, ctxd):
    _tl.remap = {}
    if not a.dry_run:
        try:
            rec = ctxd["srecs"][sid]
            cfg = mt.resolve("pos.config", mt.m2o_id(rec["config_id"])) if rec.get("config_id") else None
            if cfg:
                _tl.remap = ensure_methods(cfg, session_methods(sid))
        except Exception as e:
            log.warning("session#%s: payment methods config mein jodne mein masla: %s", sid, str(e)[:250])
    try:
        return _orig_session(sid, a, ctxd)
    finally:
        _tl.remap = {}


mp.process_session = process_session_ensure

_orig_convert = mt.convert


def convert_remap(rec, plan, dfields, strict, label):
    vals, un = _orig_convert(rec, plan, dfields, strict, label)
    if label == mp.PAY:
        rm = getattr(_tl, "remap", None)
        if rm and vals.get("payment_method_id") in rm:
            vals["payment_method_id"] = rm[vals["payment_method_id"]]
    return vals, un


mt.convert = convert_remap


def _connect():
    mt.src, mt.dst, mt.MAP = mt.Src(), mt.Dst(), mt.IdMap()


def cmd_fix_open_sessions():
    """Ctrl+C / error se jo migrate hui sessions 'opened' reh gayin unhe wapis source ki state par laao.
    (Warna agli session par 'Another session is already opened for this point of sale' aata hai.)"""
    _connect()
    pairs = mt.MAP.tx_src_ids(mp.SESS)
    fixed = 0
    for j in range(0, len(pairs), 300):
        ch = pairs[j:j + 300]
        sst = {r["id"]: r.get("state") for r in mt.src.call(mp.SESS, "read", [p[0] for p in ch], fields=["state"], context=CTX)}
        dst_ = {r["id"]: r.get("state") for r in mt.dst.call(mp.SESS, "search_read", [("id", "in", [p[1] for p in ch])],
                                                              fields=["state"], context=CTX)}
        for sid, did in ch:
            want = mp.map_state(mp.SESS, sst.get(sid), "closed")
            if did in dst_ and dst_[did] != want:
                mt.dst.call(mp.SESS, "write", [did], {"state": want}, context=CTX)
                fixed += 1
    print("sessions ki state theek ki: %d (migrate hui sessions: %d)" % (fixed, len(pairs)))


def cmd_cleanup_clones(delete):
    """Pehle ke ghalat logic se bani faltu cash-method copies: jo kisi POS config se juri nahi."""
    _connect()
    pmf = mt.dst.fields("pos.payment.method") or {}
    fl = [f for f in ("name", "type", "is_cash_count", "config_ids") if f in pmf]
    rows = mt.dst.call("pos.payment.method", "search_read", [("config_ids", "=", False)], fields=fl, context=CTX)
    cand = [r for r in rows if " (" in (r.get("name") or "") and
            ((r.get("type") == "cash") if "type" in r else bool(r.get("is_cash_count")))]
    print("Faltu copies (cash, naam mein '(...)', kisi POS se juri nahi): %d" % len(cand))
    for r in cand:
        print("   id %s  %s" % (r["id"], r["name"]))
    if cand and delete:
        for r in cand:
            try:
                mt.dst.call("pos.payment.method", "unlink", [r["id"]], context=CTX)
            except Exception as e:
                print("   id %s delete nahi hua: %s" % (r["id"], str(e)[:150]))
        print("delete ho gayin")
    elif cand:
        print("Hatane ke liye: python3 migrate_pos_ext.py cleanup-clones --delete")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "fix-open-sessions":
        cmd_fix_open_sessions()
        sys.exit(0)
    if len(sys.argv) > 1 and sys.argv[1] == "cleanup-clones":
        cmd_cleanup_clones("--delete" in sys.argv)
        sys.exit(0)
    try:
        mp.main()
    except KeyboardInterrupt:
        if mt.MAP:
            mt.MAP.commit()
        sys.exit("Stopped by user (map save ho chuka).")
    except Exception as exc:
        log.exception("POS migration stopped")
        sys.exit(str(exc))
