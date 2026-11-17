#!/bin/bash
# arb-holding v4 (G-023, TK-534): весь счёт после разбора книг из сырья (hold_parse.py → /data/arb-hold/b3) и марок (hold_marks.py → v4/marks.pkl).
# Запуск юнитом systemd-run на srv из каталога tools/arb_stats:  bash hold_run_v4.sh <этап>...   (этапы: prep trades scan-strict scan-tol filter resim report stay all)
# Каждый этап пропускается, если его выход уже есть (удалить файл — пересчитать). Выходы — /data/arb-hold/v4/.
set -euo pipefail
W=/data/arb-hold
V=$W/v4
B=$W/b3
AUDIT=/data/collect-gap/out
PAR=${PAR:-6}
stage() { [ ! -e "$1" ]; }
run_prep() {
  stage $V/rawgaps.pkl && python3 hold_rawgaps.py $B/gaps $V/rawgaps.pkl
  if stage $V/window.json; then
    T0=$(python3 - <<EOF
import pickle
r = pickle.load(open("$V/rawgaps.pkl", "rb"))
first = min(v[0] for v in r["span"].values())
print((first + 59_999) // 60_000 * 60_000)
EOF
)
    echo "T0=$T0"
    python3 hold_gaps.py $B/parsed $T0 $V/window.json $V/dead.json 15 | tee $V/gaps.txt
  fi
  stage $V/symstatus.json && python3 hold_symstatus.py $V/symstatus.json
  return 0
}
win() {
  T0=$(python3 -c "import json;print(json.load(open('$V/window.json'))['t0'])")
  T1=$(python3 -c "import json;print(json.load(open('$V/window.json'))['t1'])")
}
for st in "$@"; do
  case $st in
  prep) run_prep ;;
  trades) stage $V/trades.pkl && python3 hold_trades.py $V/trades.pkl 3 ;;
  scan-strict) win; stage $V/scan.part000 && HOLD_SIM=0 python3 hold_scan.py $B $T0 $T1 $V/scan $PAR ;;
  scan-tol) win; stage $V/scan-tol.part000 && HOLD_SIM=0 HOLD_TOL_MS=1000 python3 hold_scan.py $B $T0 $T1 $V/scan-tol $PAR ;;
  filter)
    stage $V/table-strict.pkl && python3 hold_table.py $V/scan.part* $V/table-strict.pkl
    stage $V/table-tol.pkl && python3 hold_table.py $V/scan-tol.part* $V/table-tol.pkl
    stage $V/flag-strict.pkl && python3 hold_filter.py $V/table-strict.pkl $V/symstatus.json $V/dead.json $AUDIT $V/marks.pkl $V/rawgaps.pkl $V/flag-strict.pkl
    stage $V/flag-tol.pkl && python3 hold_filter.py $V/table-tol.pkl $V/symstatus.json $V/dead.json $AUDIT $V/marks.pkl $V/rawgaps.pkl $V/flag-tol.pkl
    ;;
  resim)
    stage $V/resim-strict.pkl && python3 hold_resim.py $V/flag-strict.pkl $B $V/resim-strict.pkl 5 $V/marks.pkl
    stage $V/resim-tol.pkl && python3 hold_resim.py $V/flag-tol.pkl $B $V/resim-tol.pkl 5 $V/marks.pkl
    ;;
  report)
    python3 hold_report.py $V/flag-strict.pkl $V/resim-strict.pkl $V/tables-strict.md $V/pairs-strict.csv $V/cases-strict.csv
    python3 hold_report.py $V/flag-tol.pkl $V/resim-tol.pkl $V/tables-tol.md $V/pairs-tol.csv $V/cases-tol.csv
    ;;
  stay)
    stage $V/stay.pkl && python3 hold_stay.py build $V/flag-strict.pkl $B $V/marks.pkl $V/trades.pkl $V/stay.pkl 5
    python3 hold_stay.py eval $V/stay.pkl $V/stay.md
    ;;
  all) bash "$0" prep trades scan-strict scan-tol filter resim report stay ;;
  *) echo "этап? $st"; exit 2 ;;
  esac
done
echo DONE
