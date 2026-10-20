#!/usr/bin/env bash
# Odoo 16 -> Odoo 20 TRANSACTION migration: steps sahi tarteeb se.
#   ./run_transactions.sh check            # masters ka gap report (kuch likhta nahi)
#   ./run_transactions.sh fix              # jo masters mil gaye unhe idmap mein likh do
#   ./run_transactions.sh test             # har step ke sirf 5 records (staging par pehle ye chalao)
#   ./run_transactions.sh all              # sab core steps (poora data)
#   ./run_transactions.sh step moves --date-from 2025-01-01 --workers 4
#   ./run_transactions.sh verify
cd "$(dirname "$0")" || exit 1
PY="python3 migrate_transactions.py"
case "$1" in
  check)  $PY check ;;
  fix)    $PY check --fix ;;
  test)   $PY run --steps core --limit 5 ;;
  all)    shift; $PY run --steps core --workers "${WORKERS:-4}" "$@" ;;
  step)   shift; s="$1"; shift; $PY run --steps "$s" "$@" ;;
  verify) $PY verify ;;
  *) echo "Use: check | fix | test | all | step <name> [opts] | verify   (names: python3 migrate_transactions.py list)" ;;
esac