#!/usr/bin/env bash
# Odoo 16 -> Odoo 20 master data migration: steps ko sahi tarteeb se chalata hai.
#
#   ./run_migration.sh list        # steps ki list
#   ./run_migration.sh 1           # sirf step 1
#   ./run_migration.sh 1 2 3       # steps 1, 2, 3 (is tarteeb se)
#   ./run_migration.sh all         # step 1 se 9, phir 11 (websites), phir 10 (final pass)
#   ./run_migration.sh dry         # kuch likhe baghair sab ka count/check
#
# Har step dobara chalana safe hai (idmap.json yaad rakhta hai, duplicate nahi banta).
# idmap.json DELETE MAT KARO (jab tak naya migration shuru na karna ho).

cd "$(dirname "$0")" || exit 1
PY="python3 odoo16_to_20_migrate.py"

errs() { grep -c " ERROR " migration.log 2>/dev/null || true; }

run() {            # run "<naam>" <models> [extra args]
  local name="$1" models="$2"; shift 2
  echo; echo "================ $name ================"
  local before; before=$(errs); before=${before:-0}
  $PY migrate --only "$models" "$@"
  local after; after=$(errs); after=${after:-0}
  local new=$((after - before))
  if [ "$new" -gt 0 ]; then
    echo ">>> $new NAYE ERRORS. Aakhri 5 errors:"; grep " ERROR " migration.log | tail -5
    echo ">>> Pehle inhe theek karo, phir ye step dobara chalao."
  else
    echo ">>> OK (naye errors: 0)"
  fi
}

step1()  { run "1. Finance base" "res.currency,res.currency.rate,account.account,account.tax.group,account.tax,account.journal,account.fiscal.position,account.fiscal.position.tax,account.fiscal.position.account"; }
step2()  { run "2. Units of Measure" "uom.uom"; }
step3()  { run "3. Categories & attributes" "product.category,pos.category,product.attribute,product.attribute.value"; }
step4()  { run "4. Users & HR base" "res.users,hr.department,hr.job,resource.calendar,resource.calendar.attendance,hr.work.location,hr.employee.category,hr.departure.reason,hr.employee"; }
step5()  { run "5. Partners (customers/vendors)" "res.country.state,res.partner.category,res.partner.industry,res.partner"; }
step6()  { run "6. Products & pricelists" "product.template,product.template.attribute.line,product.product,product.supplierinfo,product.pricelist"; }
step7()  { run "7. Stock (warehouse, locations, rules)" "stock.warehouse,stock.location,stock.route,stock.rule,stock.picking.type"; }
step8()  { run "8a. POS payment methods" "pos.payment.method"
           run "8b. POS configs (sirf active)" "pos.config" --domain "[('active','=',True)]"; }
step9()  { run "9. Payroll" "hr.work.entry.type,hr.salary.rule.category,hr.payroll.structure.type,hr.payroll.structure,hr.rule.parameter,hr.rule.parameter.value"; }
step11() { run "11a. Websites + images" "website,ir.attachment"
           run "11b. Pages, menus, redirects" "website.page,website.menu,website.rewrite"
           run "11c. Blog" "blog.blog,blog.tag.category,blog.tag,blog.post"
           run "11d. eCommerce categories & ribbons" "product.public.category,product.ribbon"
           run "11e. Products dobara (website fields: eCommerce category, published)" "product.template,product.product"; }
step12() { run "12a. Payment terms & partner bank accounts" "account.payment.term,res.partner.bank"
           run "12b. Pricelist rules" "product.pricelist.item"
           run "12c. Employee contracts (hr.version)" "hr.contract"
           run "12d. Payroll salary rules" "hr.salary.rule"
           run "12e. Reorder rules" "stock.warehouse.orderpoint"
           run "12f. Links jodna (payment terms, bank accounts partners/journals/products par)" "account.journal,res.partner,product.template,product.product"; }
step10() { echo; echo "================ 10. Final pass (adhoore links jorna) ================"
           $PY migrate
           echo ">>> unresolved.csv dekho (jo links ab bhi adhoore hain)."; }

case "$1" in
  list) cat <<'TXT'
 1  Finance base         currency, rates, accounts, taxes, journals, fiscal positions
 2  UoM                  units of measure
 3  Categories           product.category, pos.category, attributes
 4  Users & HR base      users, departments, jobs, calendars, employees
 5  Partners             customers / vendors / contacts
 6  Products             templates, variants, vendor prices, pricelists
 7  Stock                warehouses, locations, routes, rules, operation types
 8  POS                  payment methods, POS configs (active only)
 9  Payroll              work entry types, salary categories, structures, parameters
10  Final pass           sab ko ek saath dobara (cross-model links jorta hai)
12  Round-2 masters     payment terms, bank accounts, pricelist rules, contracts, salary rules, reorder rules
11  Websites             website, images, pages, menus, blog, eCommerce categories (sirf ye step chalao: ./run_migration.sh 11)
TXT
  ;;
  dry) $PY migrate --dry-run ;;
  all) for f in step1 step2 step3 step4 step5 step6 step7 step8 step9 step12 step11 step10; do $f; done ;;
  "") echo "Use: ./run_migration.sh list | dry | all | <step numbers>" ;;
  *) for n in "$@"; do "step$n" 2>/dev/null || echo "step $n nahi mila"; done ;;
esac