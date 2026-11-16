#!/bin/bash
# arb-holding v2: фильтры + пересчёт исполнимости + таблицы для двух вариантов эпизодов (строгий и допускающий). Запуск юнитом systemd-run на srv из каталога tools/arb_stats:
#   bash hold_run_v2.sh strict | tol | tol-need
# Предусловия: /data/arb-hold/{table.pkl, symstatus.json, dead.json, marks.pkl, marks-old.pkl, raw.pkl}; для tol — v2/scan-tol.part* (hold_scan.py при HOLD_TOL_MS=1000 HOLD_SIM=0).
set -euo pipefail
W=/data/arb-hold
V=$W/v2
MARKS=$W/marks.pkl,$W/marks-old.pkl
AUDIT=/data/collect-gap/out
case "$1" in
strict)
  python3 hold_filter.py $W/table.pkl $W/symstatus.json $W/dead.json $AUDIT $MARKS $W/raw.pkl $V/flag-strict.pkl
  python3 hold_resim.py $V/flag-strict.pkl $W/b2 $V/resim-strict.pkl 5 $MARKS
  python3 hold_report.py $V/flag-strict.pkl $V/resim-strict.pkl $V/tables-strict.md $V/pairs-strict.csv $V/cases-strict.csv
  ;;
tol-need)   # окна сырья для допускающих эпизодов, не покрытые need.pkl (строгие)
  python3 hold_table.py $V/scan-tol.part* $V/table-tol.pkl
  python3 hold_filter.py $V/table-tol.pkl $W/symstatus.json $W/dead.json $AUDIT $MARKS - $V/flag-tol0.pkl
  python3 hold_filter.py need $V/flag-tol0.pkl $V/need-tol.pkl $W/need.pkl
  ;;
tol)
  python3 hold_filter.py $V/table-tol.pkl $W/symstatus.json $W/dead.json $AUDIT $MARKS $W/raw.pkl,$V/raw-tol.pkl $V/flag-tol.pkl
  python3 hold_resim.py $V/flag-tol.pkl $W/b2 $V/resim-tol.pkl 5 $MARKS
  python3 hold_report.py $V/flag-tol.pkl $V/resim-tol.pkl $V/tables-tol.md $V/pairs-tol.csv $V/cases-tol.csv
  ;;
esac
echo DONE
