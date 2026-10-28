import re
import migrate_transactions as mt
mt.src, mt.dst = mt.Src(), mt.Dst()
C = {"active_test": False}
IMG = re.compile(r'/web/(?:image|content)/[^"\'\s)>&]+')

print("== DST websites ==")
webs = mt.dst.call("website", "search_read", [], fields=["name"], context=C)
print(webs)

print("\n== DST pages ==")
pages = mt.dst.call("website.page", "search_read", [], fields=["url", "website_id", "view_id"], context=C)
for p in pages:
    print("  ", p["id"], p["url"], p["website_id"])

print("\n== DST menus (page_id) ==")
for m in mt.dst.call("website.menu", "search_read", [["parent_id", "!=", False]],
                     fields=["name", "url", "page_id", "website_id"], context=C):
    print("  ", m["id"], m["name"], "|", m["url"], "| page:", m["page_id"], "| web:", m["website_id"])

print("\n== SOURCE: image URL patterns (website 1 aur 3 ke views) ==")
vs = mt.src.call("ir.ui.view", "search_read",
                 [["website_id", "in", [1, 3]], ["arch_db", "ilike", "/web/image"]],
                 fields=["key", "name", "website_id", "arch_db"], context=C)
for v in vs:
    found = sorted(set(IMG.findall(v["arch_db"] or "")))
    odd = [f for f in found if not re.match(r"/web/(image|content)/(ir\.attachment/)?\d+", f)]
    print("  view %-5s web=%s %-45s total=%d  non-attachment=%s"
          % (v["id"], v["website_id"][0], (v["key"] or v["name"])[:45], len(found), odd[:6]))
