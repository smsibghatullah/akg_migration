#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Website module ka read-only inventory (source vs destination). Kuch write nahi karta."""
import migrate_transactions as mt

mt.src, mt.dst = mt.Src(), mt.Dst()
CTX = {"active_test": False}

MODELS = [
    "website", "website.page", "website.menu", "website.rewrite", "website.visitor",
    "website.snippet.filter", "ir.ui.view", "ir.attachment",
    "blog.blog", "blog.post", "blog.tag", "blog.tag.category",
    "product.public.category", "product.ribbon",
    "slide.channel", "forum.forum", "event.event", "res.lang",
]


def count(c, model, domain=None):
    try:
        return c.call(model, "search_count", domain or [], context=CTX)
    except Exception as e:
        return "NA(%s)" % str(e)[:40].replace("\n", " ")


print("%-26s %10s %10s" % ("model", "SOURCE", "DEST"))
for m in MODELS:
    print("%-26s %10s %10s" % (m, count(mt.src, m), count(mt.dst, m)))

print("\n-- Website se jude ir.ui.view / attachments --")
for side, c in (("SRC", mt.src), ("DST", mt.dst)):
    print(side, "views website_id set :", count(c, "ir.ui.view", [["website_id", "!=", False]]))
    print(side, "attachments (website/view/blog):",
          count(c, "ir.attachment", [["res_model", "in", ["ir.ui.view", "website", "blog.post", "blog.blog"]]]))
    print(side, "products website_published :",
          count(c, "product.template", [["website_published", "=", True]]))

print("\n-- Websites --")
for side, c in (("SRC", mt.src), ("DST", mt.dst)):
    try:
        for w in c.call("website", "search_read", [], fields=["name", "domain", "default_lang_id"], context=CTX):
            print(side, w)
    except Exception as e:
        print(side, "FAIL", str(e)[:100])

print("\n-- Source pages --")
try:
    for p in mt.src.call("website.page", "search_read", [], fields=["name", "url", "is_published", "website_id"], context=CTX):
        print("  ", p["id"], p["url"], "|", p["name"], "| pub:", p["is_published"], "|", p["website_id"])
except Exception as e:
    print("FAIL", str(e)[:100])

print("\n-- Destination pages --")
try:
    for p in mt.dst.call("website.page", "search_read", [], fields=["name", "url", "is_published", "website_id"], context=CTX):
        print("  ", p["id"], p["url"], "|", p["name"], "| pub:", p["is_published"], "|", p["website_id"])
except Exception as e:
    print("FAIL", str(e)[:100])

print("\n-- Field farq (jo source mein hain, dest mein nahi) --")
for m in ["website", "website.page", "website.menu", "blog.post", "blog.blog", "product.public.category"]:
    try:
        sf, df = mt.src.fields(m) or {}, mt.dst.fields(m) or {}
        if not sf or not df:
            print(m, ": ek taraf model nahi"); continue
        miss = sorted(k for k in sf if k not in df and not sf[k].get("readonly") and sf[k].get("type") not in ("one2many",))
        print(m, "-> dest mein nahi:", miss[:25])
    except Exception as e:
        print(m, "FAIL", str(e)[:80])
