#!/usr/bin/env bash
# TK-216 п.3: стена волны F1 01–15.01 (13 суток B1). A = текущая: bounce-grid по всем клеткам (day.sh, наборы --set из планов F1).
# B = пул (один набор pool:age=2700,side=bid, те же формы) + раннер filter-cells.py по тем же клеткам. Каждый запуск — под /data/benchrun.sh wave.
#   tk216-wall.sh <A|B> <метка>   env: D=3 GT=2   выход /data/tk216/wall/<метка>/{wall.txt,done}; B: outR/ (клетки раннера)
set -uo pipefail
V=$1; tag=$2; D=${D:-3}; export GT=${GT:-2}
PL=/data/tk0196/plan; W=/data/tk216/wall/$tag; T=/data/tk083/tools; MO=2026-01
days=$(awk -F, 'NR>1&&$1=="jan"&&$2<="2026-01-15"{print $2}' /data/tk0115/delta/b1-symdays.csv | sort -u)
rm -rf "$W"; mkdir -p "$W/out"
if [ "$V" = B ]; then
  PB=$W/planB; mkdir -p $PB
  { sort -u $PL/f1.args $PL/f1-2026-01.args | grep -v '^--set'; echo "--set pool:age=2700,side=bid"; } > $PB/f1.args
  cat $PL/f1.cells $PL/f1-2026-01.cells | awk '{print $1" pool"}' | sort -u > $PB/f1.cells; : > $PB/f1-2026-01.cells; cp $PB/f1.args $PB/f1-2026-01.args
  export P=$PB
else export P=$PL; fi
export O=$W/out
s=$(date +%s)
echo $days | tr ' ' '\n' | xargs -P "$D" -I{} bash /data/tk0196/day.sh {} jan {}
g=$(date +%s); echo "grid_s=$((g-s))" > $W/wall.txt
fail=$(cat $W/out/*/fail.txt 2>/dev/null | wc -l); echo "fail_lines=$fail" >> $W/wall.txt
if [ "$V" = B ]; then
  mkdir -p $W/tree/$MO $W/regime
  for d in $days; do mkdir -p $W/tree/$MO/$d/pool; for f in rounds.csv signals.csv; do ln -sf $W/out/$d/out/$d/pool/$f $W/tree/$MO/$d/pool/$f; done; ln -sf /data/tk0196/regime/jan/$d.csv $W/regime/$d.csv; done
  # клетки: набор -> формы (из f1*.cells), фильтр набора -> из --set планов F1
  sort -u $PL/f1.args $PL/f1-2026-01.args | grep '^--set' | sed 's/^--set //' | awk -F: '{n=$1; sub(/^[^:]*:/,""); print n"\t"$0}' > $W/sets.tsv
  cat $PL/f1.cells $PL/f1-2026-01.cells | awk '{f[$2]=f[$2]?f[$2]","$1:$1} END{for(k in f)print k"\t"f[k]}' | sort > $W/setforms.tsv
  join -t$'\t' $W/setforms.tsv <(sort $W/sets.tsv) | awk -F'\t' '{print $1"\t"$2"\t"$3}' > $W/cells.tsv
  python3 /data/tk216/filter-cells-c.py --pool-tree $W/tree/$MO --pool-set pool --pool-base age=2700,side=bid \
    --approaches '/data/p12r3a/jrn/{day}/wt/sub/D20' --regime-from $W/regime --cells $W/cells.tsv --out $W/outR --tools $T \
    --psim-deposit 2500 --psim-position 500 --psim-max-pos 0 > $W/runner.log 2>&1 || echo "FAIL runner" >> $W/wall.txt
  echo "runner_s=$(( $(date +%s)-g ))" >> $W/wall.txt
fi
echo "wall_s=$(( $(date +%s)-s ))" >> $W/wall.txt
touch $W/done
