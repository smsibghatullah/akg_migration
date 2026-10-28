import re, sys
import migrate_transactions as mt
mt.dst = mt.Dst()
C = {"active_test": False}
apply = "--apply" in sys.argv

BRANDS = {"facebook": "fa-facebook-f", "linkedin": "fa-linkedin-in", "youtube": "fa-youtube",
          "instagram": "fa-instagram", "twitter": "fa-x-twitter", "whatsapp": "fa-whatsapp"}

def fix(arch):
    n = arch
    # FA4 -> FA6 brands
    for k, v in BRANDS.items():
        n = re.sub(r'class="fa fa-%s\b' % k, 'class="fa-brands %s' % v, n)
        n = re.sub(r'class="fa cy-icon o_editable_media fa-%s"' % k,
                   'class="fa-brands %s rounded-circle shadow-sm o_editable_media"' % v, n)
    n = n.replace("fa-twitter ", "fa-brands fa-x-twitter ")
    # WhatsApp duplicates: sirf pehla rakho
    pat = re.compile(r'<a class="cy_whatsapp_web s_social_media_whatsapp".*?</a>', re.S)
    found = pat.findall(n)
    if len(found) > 1:
        first = [True]
        def keep(m):
            if first[0]:
                first[0] = False
                return m.group(0)
            return ""
        n = pat.sub(keep, n)
    return n

for vid in (3435,):  # website 1 footer; website 2 ka id neeche dhoondta hai
    pass

views = mt.dst.call("ir.ui.view", "search_read",
                    [["key", "=", "website.footer_custom"], ["website_id", "in", [1, 2]]],
                    fields=["website_id", "arch_db"], context=C)
for v in views:
    new = fix(v["arch_db"])
    print("view", v["id"], "web", v["website_id"], "badlav:", new != v["arch_db"],
          "| whatsapp:", v["arch_db"].count("s_social_media_whatsapp"), "->", new.count("s_social_media_whatsapp"))
    if apply and new != v["arch_db"]:
        open("footer_%s_before_icons.xml" % v["id"], "w").write(v["arch_db"])
        mt.dst.call("ir.ui.view", "write", [v["id"]], {"arch": new}, context=C)
        print("   theek kiya")

# header phone (sirf website 1 ke views mein)
if apply:
    for v in mt.dst.call("ir.ui.view", "search_read", [["arch_db", "ilike", "+1 555-555-5556"]],
                         fields=["key", "arch_db"], context=C):
        a = v["arch_db"].replace("+1 555-555-5556", "+968 97 92 6449")
        mt.dst.call("ir.ui.view", "write", [v["id"]], {"arch": a}, context=C)
        print("phone badla:", v["key"])
print("done" if apply else "dry-run, --apply lagayein")
