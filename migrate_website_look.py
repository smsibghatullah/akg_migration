#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Source website 3 ka logo/favicon/social/footer -> Odoo 20 website 1.
  python3 migrate_website_look.py            # dry-run
  python3 migrate_website_look.py --apply
"""
import argparse
import re

import migrate_transactions as mt
import migrate_website as mw

C = {"active_test": False}
SRC_WEB, DST_WEB, SRC_FOOTER_VIEW = 3, 1, 3712
SOCIAL = ["social_facebook", "social_twitter", "social_linkedin", "social_youtube", "social_instagram"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    mt.src, mt.dst, mt.MAP = mt.Src(), mt.Dst(), mt.IdMap()
    mw.mt = mt

    sdf, ddf = mt.dst.fields("website") or {}, mt.dst.fields("res.company") or {}
    sw = mt.src.call("website", "read", [SRC_WEB],
                     fields=["logo", "favicon"] + SOCIAL, context=C)[0]

    # 1) website vals
    def b64(v):
        if isinstance(v, (bytes, bytearray)):
            v = v.decode()
        if isinstance(v, str) and v.startswith("data:") and "," in v:
            v = v.split(",", 1)[1]
        return v.strip() if isinstance(v, str) else v
    wvals = {k: b64(sw[k]) for k in ("logo", "favicon") if sw.get(k) and k in sdf}
    print("Website fields:", {k: "<binary>" for k in wvals})

    # 2) company vals
    cid = mt.dst.call("website", "read", [DST_WEB], fields=["company_id"], context=C)[0]["company_id"][0]
    cvals = {k: sw[k] for k in SOCIAL if sw.get(k) and k in ddf}
    if "email" in ddf:
        cvals["email"] = "info@akghardware.com"
    cur = mt.dst.call("res.company", "read", [cid], fields=["phone", "email"], context=C)[0]
    print("Dest company abhi:", cur)
    print("Company vals:", cvals)

    # 3) footer
    sv = mt.src.call("ir.ui.view", "read", [SRC_FOOTER_VIEW], fields=["arch_db"], context=C)[0]
    refs = []
    arch = mw.remap(sv["arch_db"], a.apply, refs)
    arch = re.sub(r'\s+t-name="[^"]*"', "", arch, count=1)
    dv = mt.dst.call("ir.ui.view", "search_read",
                     [["key", "=", "website.footer_custom"], ["website_id", "in", [DST_WEB, False]]],
                     fields=["website_id", "arch_db"], context=C)
    dv.sort(key=lambda v: 0 if v["website_id"] else 1)
    print("Footer: src arch=%d bytes, images=%d | dest views=%s"
          % (len(arch), len(refs), [(v["id"], v["website_id"]) for v in dv]))
    if not dv:
        print("Dest mein website.footer_custom nahi mila, footer skip.")

    if not a.apply:
        print("\nYeh sirf dry-run tha. --apply lagayein.")
        return

    for k, v in wvals.items():
        try:
            mt.dst.call("website", "write", [DST_WEB], {k: v}, context=C)
            print("OK  website.%s" % k)
        except Exception as e:
            print("FAIL website.%s: %s  (UI se upload karein)" % (k, str(e)[:120]))
    if cvals:
        mt.dst.call("res.company", "write", [cid], cvals, context=C)
    if dv:
        open("dst_footer_backup.xml", "w").write(dv[0]["arch_db"] or "")
        mt.dst.call("ir.ui.view", "write", [dv[0]["id"]], {"arch": arch},
                    context=dict(C, website_id=DST_WEB))
    mt.MAP.db.commit()
    print("Done. Footer backup: dst_footer_backup.xml")


if __name__ == "__main__":
    main()
