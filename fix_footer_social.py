import sys
import migrate_transactions as mt
mt.dst = mt.Dst()
C = {"active_test": False}
apply = "--apply" in sys.argv
FB, IG = "https://www.facebook.com/akg.hardware/", "http://instagram.com/akg_hardware"
LINKS = {
    1: {"facebook": FB, "linkedin": "https://www.linkedin.com/company/akg-hardware/",
        "youtube": "https://www.youtube.com/@AKGHardware/", "instagram": IG},
    2: {"facebook": FB, "twitter": "https://twitter.com/Al_KhaleelGroup",
        "linkedin": "https://om.linkedin.com/company/akg-hardware",
        "youtube": "https://www.youtube.com/@alkhaleelgroup1832", "instagram": IG},
}
for wid, links in LINKS.items():
    v = mt.dst.call("ir.ui.view", "search_read",
                    [["key", "=", "website.footer_custom"], ["website_id", "=", wid]],
                    fields=["arch_db"], context=C)
    if not v:
        print("website", wid, "footer nahi"); continue
    a = v[0]["arch_db"]; n = a
    for k, u in links.items():
        n = n.replace('href="/website/social/%s"' % k, 'href="%s"' % u)
    print("website", wid, "badlav:", n != a)
    if apply and n != a:
        open("footer_w%s_before_social.xml" % wid, "w").write(a)
        mt.dst.call("ir.ui.view", "write", [v[0]["id"]], {"arch": n}, context=C)
        print("  theek kiya")
