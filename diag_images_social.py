import re, sys
import migrate_transactions as mt
mt.src, mt.dst = mt.Src(), mt.Dst()
C = {"active_test": False}
apply = "--apply" in sys.argv
IMG = re.compile(r'<img[^>]+>')
SRC = re.compile(r'(?:data-)?src="([^"]+)"')

print("== SOURCE home (view 3711) img tags ==")
a = mt.src.call("ir.ui.view", "read", [3711], fields=["arch_db"], context=C)[0]["arch_db"]
for t in IMG.findall(a):
    print("  ", SRC.findall(t)[:1], "| data-original-id" in t)

print("\n== DEST home (website 1) img tags ==")
pg = mt.dst.call("website.page", "search_read", [["url", "=", "/"], ["website_id", "=", 1]],
                 fields=["view_id"], context=C)[0]
d = mt.dst.call("ir.ui.view", "read", [pg["view_id"][0]], fields=["arch_db"], context=C)[0]["arch_db"]
for t in IMG.findall(d):
    s = (SRC.findall(t) or [""])[0]
    m = re.search(r'/web/image/(\d+)', s)
    info = ""
    if m:
        r = mt.dst.call("ir.attachment", "read", [int(m.group(1))],
                        fields=["name", "public", "mimetype", "file_size", "res_model"], context=C)
        info = r[0] if r else "ATTACHMENT NAHI"
    print("  ", s, "->", info)

print("\n== DEST footer social references ==")
fv = mt.dst.call("ir.ui.view", "search_read",
                 [["key", "=", "website.footer_custom"], ["website_id", "=", 1]],
                 fields=["arch_db"], context=C)
if fv:
    arch = fv[0]["arch_db"]
    print("  ", sorted(set(re.findall(r'[\w.]*social_\w+', arch))))
    new = arch.replace("website.social_", "res_company.social_")
    print("   badlav hoga:", new != arch)
    if apply and new != arch:
        mt.dst.call("ir.ui.view", "write", [fv[0]["id"]], {"arch": new}, context=C)
        print("   footer theek kiya")
