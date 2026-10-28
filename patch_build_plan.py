#!/usr/bin/env python3
"""migrate_transactions.py mein build_plan() ko Odoo 20 ke liye theek karta hai.
Odoo 16: hr.leave / hr.leave.allocation par  holiday_status_id (-> hr.leave.type)
Odoo 20: wahi field                         work_entry_type_id (-> hr.work.entry.type)
Backup khud banata hai (migrate_transactions.py.bak_leave). Dobara chalane par kuch nahi badalta.
Chalana:  cd ~/akg_migration && python3 patch_build_plan.py
"""
import os, shutil, sys

F = "migrate_transactions.py"
OLD = '        plan[f] = (f, s["type"], d["type"], rel)\n    return plan\n'
NEW = '''        plan[f] = (f, s["type"], d["type"], rel)
    # --- Odoo 20: holiday_status_id (hr.leave.type) -> work_entry_type_id (hr.work.entry.type) ---
    if ("holiday_status_id" in sf and "holiday_status_id" not in df and "work_entry_type_id" in df
            and sf["holiday_status_id"]["type"] == "many2one"):
        plan["holiday_status_id"] = ("work_entry_type_id", "many2one", "many2one",
                                     sf["holiday_status_id"].get("relation") or "hr.leave.type")
    return plan
'''
if not os.path.exists(F):
    sys.exit("%s nahi mili - ~/akg_migration mein chalayein" % F)
s = open(F, encoding="utf-8").read()
if "work_entry_type_id" in s and "Odoo 20: holiday_status_id" in s:
    sys.exit("Patch pehle se lagi hui hai, kuch nahi badla.")
if s.count(OLD) != 1:
    sys.exit("build_plan ka expected code %d baar mila (1 hona chahiye). File mat badlein, mujhe batayein." % s.count(OLD))
shutil.copy(F, F + ".bak_leave")
open(F, "w", encoding="utf-8").write(s.replace(OLD, NEW))
print("Patch lag gayi. Backup: %s.bak_leave" % F)
