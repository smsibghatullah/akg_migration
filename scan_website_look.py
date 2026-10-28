#!/usr/bin/env python3
"""Read-only: source website 3 aur 1 ki look-related settings + custom views dump karta hai."""
import migrate_transactions as mt
mt.src, mt.dst = mt.Src(), mt.Dst()
C = {"active_test": False}

sf = mt.src.fields("website") or {}
want = ["name", "domain", "logo", "favicon", "social_facebook", "social_twitter", "social_linkedin",
        "social_youtube", "social_instagram", "social_github", "company_id", "custom_code_head", "custom_code_footer"]
flds = [f for f in want if f in sf]
for w in mt.src.call("website", "search_read", [["id", "in", [1, 3]]], fields=flds, context=C):
    print("SRC website", w["id"], w["name"])
    for k, v in w.items():
        if k in ("id", "name"): continue
        print("   %-20s %s" % (k, ("<binary %d chars>" % len(v)) if k in ("logo", "favicon") and v else v))

print("\nSRC company (website 3):")
w3 = mt.src.call("website", "read", [3], fields=["company_id"], context=C)[0]["company_id"]
cf = mt.src.fields("res.company") or {}
cw = [f for f in ["name", "phone", "email", "website", "social_facebook", "social_twitter",
                  "social_linkedin", "social_youtube", "social_instagram"] if f in cf]
print("  ", mt.src.call("res.company", "read", [w3[0]], fields=cw, context=C))

print("\nDST website/company:")
print("  ", mt.dst.call("website", "search_read", [], fields=["name", "company_id"], context=C))

print("\nSRC custom views (website 1 / 3):")
vs = mt.src.call("ir.ui.view", "search_read",
                 [["website_id", "in", [1, 3]], ["key", "!=", False]],
                 fields=["key", "name", "website_id", "active", "arch_db"], context=C)
import re, os
os.makedirs("website_views", exist_ok=True)
for v in sorted(vs, key=lambda x: x["key"]):
    print("  %-5s web=%s active=%-5s %-55s %6d" % (v["id"], v["website_id"][0], v["active"], v["key"], len(v["arch_db"] or "")))
    if any(t in v["key"] for t in ("footer", "header", "layout", "brand")):
        fn = "website_views/w%s_%s.xml" % (v["website_id"][0], re.sub(r"[^a-z0-9_]+", "_", v["key"]))
        open(fn, "w").write(v["arch_db"] or "")
print("\nFooter/header/layout views files mein save: website_views/")
