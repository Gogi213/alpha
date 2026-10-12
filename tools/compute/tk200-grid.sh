#!/bin/bash
# сетка 9 мес. одной механики (TK-200): tk200-grid.sh <плагин> [work]  (work = KIND rounds/keep: рабочий каталог цепочки на месяц)
cd /data/tk200/tools/registry || exit 1
rc=0
for m in 01 02 03 04 05 06 07 08 09; do
  out=/data/tk200/grid/$1-2026-$m.csv
  [ -s "$out" ] && continue
  extra=""; [ "$2" = work ] && extra="--work /data/tk200/grid/$1-2026-$m.work"
  python3 closure_mech.py --mechanic "$1" --month 2026-$m $extra --out "$out" > /data/tk200/grid/$1-2026-$m.log 2>&1 || rc=1
done
exit $rc
