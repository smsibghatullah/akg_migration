#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Odoo 16 website 3 (AKG Hardware) ke pages -> Odoo 20 website 1.
  python3 migrate_website.py                 # dry-run (kuch write nahi)
  python3 migrate_website.py --apply
  python3 migrate_website.py --apply --with-portal   # /employee-portal (src page 4) bhi
Shart: koi aur migrate_* process na chal raha ho. Pehle destination ka backup lein.
"""
import argparse
import re

import migrate_transactions as mt

CTX = {"active_test": False}
DST_WEBSITE = 1
PAGES = [8, 9, 10]          # "/" (naya wala), /locations, /cookie-policy
PORTAL_PAGES = [4]          # /employee-portal (website 1 ka published page)
REF = re.compile(r"/web/(image|content)/(?:ir\.attachment/)?(\d+)")


# ---- write helpers: signature migrate_transactions.Dst.call ke mutabiq check karein ----
def dst_create(model, vals):
    r = mt.dst.call(model, "create", vals, context=CTX)
    return r[0] if isinstance(r, list) else r


def dst_write(model, ids, vals):
    return mt.dst.call(model, "write", ids, vals, context=CTX)
# -----------------------------------------------------------------------------------------


def copy_attachment(sid, apply):
    """Source attachment ko dest mein public attachment ke taur par copy karta hai."""
    done = mt.MAP.get("website.attachment", sid)
    if done:
        return done
    a = mt.src.call("ir.attachment", "read", [sid],
                    fields=["name", "mimetype", "type", "datas"], context=CTX)
    if not a or a[0]["type"] == "url" or not a[0].get("datas"):
        return None
    if not apply:
        return -1
    a = a[0]
    did = dst_create("ir.attachment", {"name": a["name"], "mimetype": a["mimetype"],
                                       "raw": a["datas"], "public": True})
    mt.MAP.set("website.attachment", sid, did)
    mt.MAP.db.commit()
    return did


def remap(arch, apply, refs):
    def sub(m):
        sid = int(m.group(2))
        did = copy_attachment(sid, apply)
        refs.append((sid, did))
        if did in (None, -1):
            return m.group(0)
        s = m.group(0)
        return s[:m.start(2) - m.start(0)] + str(did)
    return REF.sub(sub, arch)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--with-portal", action="store_true")
    a = ap.parse_args()
    mt.src, mt.dst, mt.MAP = mt.Src(), mt.Dst(), mt.IdMap()

    ids = PAGES + (PORTAL_PAGES if a.with_portal else [])
    for pid in ids:
        if mt.MAP.get("website.page", pid):
            print("page %s pehle se migrate hai, skip" % pid)
            continue
        p = mt.src.call("website.page", "read", [pid],
                        fields=["name", "url", "is_published", "view_id"], context=CTX)[0]
        v = mt.src.call("ir.ui.view", "read", [p["view_id"][0]], fields=["arch_db"], context=CTX)[0]
        refs = []
        arch = remap(v["arch_db"], a.apply, refs)
        arch = re.sub(r'\s+t-name="[^"]*"', "", arch, count=1)
        tcalls = sorted(set(re.findall(r't-call="([^"]+)"', arch)))
        print("page %-3s %-18s arch=%6d bytes | images/files=%d | t-call=%s"
              % (pid, p["url"], len(arch), len(refs), tcalls))

        ex = mt.dst.call("website.page", "search_read",
                         [["url", "=", p["url"]], ["website_id", "=", DST_WEBSITE]],
                         fields=["view_id"], context=CTX)
        if ex:
            print("   dest mein page maujood (view %s): arch replace hoga" % ex[0]["view_id"][0])
            if a.apply:
                dst_write("ir.ui.view", [ex[0]["view_id"][0]], {"arch": arch})
                mt.MAP.set("website.page", pid, ex[0]["id"])
            continue
        print("   dest mein naya page banega")
        if a.apply:
            slug = re.sub(r"[^a-z0-9]+", "_", p["url"].lower()).strip("_") or "home"
            vid = dst_create("ir.ui.view", {"name": p["name"], "type": "qweb", "arch": arch,
                                            "key": "website.akg_" + slug, "website_id": DST_WEBSITE})
            did = dst_create("website.page", {"url": p["url"], "view_id": vid,
                                              "website_id": DST_WEBSITE,
                                              "is_published": p["is_published"]})
            mt.MAP.set("website.page", pid, did)
    if a.apply:
        dst_write("website", [DST_WEBSITE], {"name": "AKG Hardware"})
        mt.MAP.db.commit()
        print("Done.")
    else:
        print("\nYeh sirf dry-run tha. --apply lagayein.")


if __name__ == "__main__":
    main()
