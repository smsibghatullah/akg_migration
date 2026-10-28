import sys
import migrate_transactions as mt
mt.dst = mt.Dst()
C = {"active_test": False}
apply = "--apply" in sys.argv

pages = mt.dst.call("website.page", "search_read", [], fields=["url", "website_id"], context=C)
menus = mt.dst.call("website.menu", "search_read", [["parent_id", "!=", False]],
                    fields=["name", "url", "page_id", "website_id"], context=C)

for m in menus:
    if m["page_id"] or not m["url"].startswith("/") or m["url"] == "/shop":
        continue
    mw = m["website_id"][0] if m["website_id"] else False
    cand = [p for p in pages if p["url"] == m["url"] and (p["website_id"][0] if p["website_id"] else False) in (mw, False)]
    if cand:
        print("LINK menu %s %s -> page %s" % (m["id"], m["url"], cand[0]["id"]))
        if apply:
            mt.dst.call("website.menu", "write", [m["id"]], {"page_id": cand[0]["id"]}, context=C)

shop = [m["id"] for m in menus if m["url"] == "/shop"]
print("Shop menus hatenge:", shop)
if apply and shop:
    mt.dst.call("website.menu", "unlink", shop)
print("Done" if apply else "Dry-run, --apply lagayein")
