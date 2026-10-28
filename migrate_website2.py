#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Source website 1 (portal) -> Odoo 20 mein nayi website.
  python3 migrate_website2.py                          # dry-run
  python3 migrate_website2.py --apply
  python3 migrate_website2.py --apply --clean-public-menus
Pehle destination ka backup lein.
"""
import argparse
import re

import migrate_transactions as mt
import migrate_website as mw

C = {"active_test": False}
NAME = "Welcome to AKG Hardware"
PUBLIC_WEB = 1
SRC_WEB = 1
SRC_FOOTER = 3505
PUBLIC_REMOVE = ["/shop", "/s/distribution_agreement", "/appointment", "/employee-portal"]


def b64(v):
    if isinstance(v, (bytes, bytearray)):
        v = v.decode()
    return v.strip() if isinstance(v, str) else v


def safe(label, fn):
    try:
        r = fn()
        print("OK  ", label)
        return r
    except Exception as e:
        print("FAIL", label, "->", str(e)[:160].replace("\n", " "))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--clean-public-menus", action="store_true")
    a = ap.parse_args()
    mt.src, mt.dst, mt.MAP = mt.Src(), mt.Dst(), mt.IdMap()
    dfw = mt.dst.fields("website") or {}
    sfw = mt.src.fields("website") or {}

    # 1) website banao / dhoondo
    ex = mt.dst.call("website", "search_read", [["name", "=", NAME]], fields=["id"], context=C)
    wid = ex[0]["id"] if ex else None
    if wid:
        print("Website pehle se hai: id", wid)
    elif a.apply:
        wid = mt.dst.call("website", "create", {"name": NAME}, context=C)
        print("Website bani: id", wid)
    else:
        print("Nayi website banegi:", NAME, "(domain baad mein DNS par)")

    # 2) logo
    sw = mt.src.call("website", "read", [SRC_WEB],
                     fields=[f for f in ("logo", "homepage_url") if f in sfw], context=C)[0]
    print("Logo:", "hai" if sw.get("logo") else "nahi")
    if a.apply and wid and sw.get("logo"):
        safe("website.logo", lambda: mt.dst.call("website", "write", [wid], {"logo": b64(sw["logo"])}, context=C))
    if a.apply and wid and "homepage_url" in dfw:
        safe("homepage_url=/employee-portal",
             lambda: mt.dst.call("website", "write", [wid], {"homepage_url": "/employee-portal"}, context=C))

    # 3) /employee-portal page public se nayi website mein
    pg = mt.dst.call("website.page", "search_read",
                     [["url", "=", "/employee-portal"], ["website_id", "=", PUBLIC_WEB]],
                     fields=["view_id"], context=C)
    print("Public site par /employee-portal page:", [p["id"] for p in pg])
    if a.apply and wid:
        for p in pg:
            safe("view move", lambda: mt.dst.call("ir.ui.view", "write", [p["view_id"][0]], {"website_id": wid}, context=C))
            safe("page move", lambda: mt.dst.call("website.page", "write", [p["id"]], {"website_id": wid}, context=C))

    # 4) menu
    if wid:
        root = mt.dst.call("website", "read", [wid], fields=["menu_id"], context=C)[0]["menu_id"][0]
        old = mt.dst.call("website.menu", "search_read", [["parent_id", "=", root]],
                          fields=["name", "url"], context=C)
        print("Nayi website ke default menus (hatenge):", [(m["name"], m["url"]) for m in old])
    else:
        root, old = None, []
    sroot = mt.src.call("website", "read", [SRC_WEB], fields=["menu_id"], context=C)[0]["menu_id"][0]
    smenus = mt.src.call("website.menu", "search_read", [["parent_id", "=", sroot]],
                         fields=["name", "url", "sequence"], context=C)
    smenus = [m for m in sorted(smenus, key=lambda m: m["sequence"]) if m["url"] != "/shop"]
    print("Source menu (copy honge):", [(m["name"], m["url"]) for m in smenus])
    if a.apply and wid and root:
        if old:
            safe("default menus delete", lambda: mt.dst.call("website.menu", "unlink", [m["id"] for m in old]))
        for i, m in enumerate(smenus):
            safe("menu " + m["name"], lambda m=m, i=i: mt.dst.call(
                "website.menu", "create",
                {"name": m["name"], "url": m["url"], "sequence": 10 + i, "parent_id": root, "website_id": wid},
                context=C))

    # 5) footer
    sv = mt.src.call("ir.ui.view", "read", [SRC_FOOTER], fields=["arch_db"], context=C)[0]
    refs = []
    arch = mw.remap(sv["arch_db"], a.apply, refs)
    arch = re.sub(r'\s+t-name="[^"]*"', "", arch, count=1)
    print("Footer: %d bytes, images=%d" % (len(arch), len(refs)))
    if a.apply and wid:
        dv = mt.dst.call("ir.ui.view", "search_read",
                         [["key", "=", "website.footer_custom"], ["website_id", "in", [wid, False]]],
                         fields=["website_id"], context=C)
        dv.sort(key=lambda v: 0 if v["website_id"] else 1)
        if dv:
            safe("footer", lambda: mt.dst.call("ir.ui.view", "write", [dv[0]["id"]], {"arch": arch},
                                               context=dict(C, website_id=wid)))

    # 6) public site ke menu saaf
    if a.clean_public_menus:
        r1 = mt.dst.call("website", "read", [PUBLIC_WEB], fields=["menu_id"], context=C)[0]["menu_id"][0]
        bad = mt.dst.call("website.menu", "search_read",
                          [["parent_id", "=", r1], ["url", "in", PUBLIC_REMOVE]],
                          fields=["name", "url"], context=C)
        print("Public menu se hatenge:", [(m["id"], m["name"], m["url"]) for m in bad])
        if a.apply and bad:
            safe("public menus delete", lambda: mt.dst.call("website.menu", "unlink", [m["id"] for m in bad]))

    mt.MAP.db.commit()
    if not a.apply:
        print("\nYeh sirf dry-run tha. --apply lagayein.")


if __name__ == "__main__":
    main()
