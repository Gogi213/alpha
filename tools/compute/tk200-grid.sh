#!/bin/bash
# сетка 9 мес. одной механики (TK-200): tk200-grid.sh <плагин> [closes|work]
#   closes — KIND closes: по потолкам 0/3 из /data/p12r3a/closes-b1-cap<C>-<мес>.json, выход <плагин>-cap<C>-2026-<мес>.csv
#   work   — KIND keep/rounds: рабочий каталог цепочки на месяц; пусто — KIND report
#   GRID='k=v1,v2;…' ТАГ=_суффикс — сузить домен (дельта: только недосчитанные точки), выход <плагин><ТАГ>-…; без них — вся сетка
# возобновляемо: готовый (непустой) выход пропускается.
cd /data/tk200/tools/registry || exit 1
rc=0
for m in 01 02 03 04 05 06 07 08 09; do
  if [ "$2" = closes ]; then
    for c in 0 3; do
      out=/data/tk200/grid/$1${TAG}-cap$c-2026-$m.csv
      [ -s "$out" ] && continue
      python3 closure_mech.py --mechanic "$1" --month 2026-$m --closes /data/p12r3a/closes-b1-cap$c-2026-$m.json ${GRID:+--grid "$GRID"} --out "$out" > "${out%.csv}.log" 2>&1 || rc=1
    done
    continue
  fi
  out=/data/tk200/grid/$1${TAG}-2026-$m.csv
  [ -s "$out" ] && continue
  extra=""; [ "$2" = work ] && extra="--work /data/tk200/grid/$1${TAG}-2026-$m.work"
  python3 closure_mech.py --mechanic "$1" --month 2026-$m $extra ${GRID:+--grid "$GRID"} --out "$out" > /data/tk200/grid/$1${TAG}-2026-$m.log 2>&1 || rc=1
done
exit $rc
