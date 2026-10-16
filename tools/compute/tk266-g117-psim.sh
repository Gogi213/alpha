#!/bin/bash
# TK-266: closes Г-117 (tsl 5/10/20 × t1/2/4) v171c без пересчёта бэктеста (В-213): готовое дерево /data/p12r2/vtn-b (v171b, TK-194)
# -> portfolio-sim --drop TRUMP/TRX/BCH, cap 0 и 3, янв–сен -> /data/tk0266/psim/closes-g117d-cap<N>-<мес>.json (порядок клеток = r2t-2026-03-10.cells).
# Метка /data/tk0266/psim/g117d.done; сбой — g117d-fail.txt. Как p12-expo.sh r2a (--drop у vand).
set -u
O=/data/tk0266/psim; SRC=/data/p12r2/vtn-b; T=/data/tk0113/tools; DROP=TRUMPUSDT,TRXUSDT,BCHUSDT; CELLS=/data/tk065/days/r2t-2026-03-10.cells
mkdir -p $O; cd $O || exit 2; rm -f g117d.done g117d-fail.txt
args=(); while read f s; do args+=(--variant "$s@$f=$s/$f"); done < $CELLS
for mo in 2026-01 2026-02 2026-03 2026-04 2026-05 2026-06 2026-07 2026-08 2026-09; do
  for cap in 0 3; do
    python3 $T/portfolio-sim.py --epoch "$mo=$SRC:$mo" "${args[@]}" --drop $DROP --deposit-usd 2500 --position-usd 500 --max-pos $cap \
      --closes-out closes-g117d-cap$cap-$mo.json > psim-g117d-cap$cap-$mo.txt 2> psim-g117d-cap$cap-$mo.log || echo "FAIL g117d $cap $mo" >> g117d-fail.txt
  done
done
touch g117d.done
