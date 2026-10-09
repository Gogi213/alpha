#!/bin/bash
# TK-083: prep -> busy-replay -> portfolio-sim по месяцам (8 форм), закрытия в JSON
cd /data/tk083 || exit 2
T=/data/tk083/tools
for m in ${MONTHS:-2026-01 2026-02 2026-03 2026-04 2026-05 2026-06 2026-07 2026-08 2026-09 2026-10}; do
  python3 prep.py $m || exit 3
  python3 $T/busy-replay.py off/$m on/$m --sets t-bid-btc4h-q1 > on-$m.log 2>&1 || exit 4
  args=(); for f in $(cat forms.txt); do args+=(--variant "$f=t-bid-btc4h-q1/$f"); done
  python3 $T/portfolio-sim.py --epoch "$m=/data/tk083/on:$m" "${args[@]}" --deposit-usd 2500 --position-usd 500 --json psim-$m.json --closes-out closes-$m.json > psim-$m.txt 2> psim-$m.log || exit 5
  echo "ok $m" >> progress.txt
done
touch kpi.done
