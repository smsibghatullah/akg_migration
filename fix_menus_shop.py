import sys
import migrate_transactions as mt
mt.dst = mt.Dst()
C = {"active_test": False}
apply = "--apply" in sys.argv
SEQ = {"Home": 10, "Locations": 20, "Distributor Registration": 20, "Product Catalog": 30,
       "Book Material Transportation": 30, "Shop": 40, "Contact us": 50}
for wid in (1, 2):
    root = mt.dst.call("website", "read", [wid], fields=["menu_id"], context=C)[0]["menu_id"][0]
    kids = mt.dst.call("website.menu", "search_read", [["parent_id", "=", root]],
                       fields=["name", "url", "sequence"], context=C)
    print("website", wid, [(k["name"], k["url"], k["sequence"]) for k in kids])
    if not any(k["url"] == "/shop" for k in kids):
        print("  Shop banega")
        if apply:
            mt.dst.call("website.menu", "create",
                        {"name": "Shop", "url": "/shop", "parent_id": root, "website_id": wid,
                         "sequence": 40}, context=C)
    for k in kids:
        s = SEQ.get(k["name"])
        if apply and s and s != k["sequence"]:
            mt.dst.call("website.menu", "write", [k["id"]], {"sequence": s}, context=C)
print("done" if apply else "dry-run, --apply lagayein")
