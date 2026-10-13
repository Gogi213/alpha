#!/bin/bash
# TK-196: сделки F1 (319 клеток × 251 сутки, /data/tk0196/out/<сутки>/out/<сутки>) -> деревья по месяцу -> busy-replay -> portfolio-sim cap 0 и 3
# с --drop TRUMP/TRX/BCH (v171c, В-210) -> /data/tk0196/psim/closes-f1-cap<N>-<мес>.json (порядок клеток = cells.txt месяца). Как tk120-r2b-psim.sh. Метка psim-f1.done.
set -u
O=/data/tk0196/psim; R=/data/tk0196/out; TB=/data/tk083/tools; T=/data/tk0113/tools; DROP=TRUMPUSDT,TRXUSDT,BCHUSDT; P=${PM:-3}
mkdir -p $O; cd $O || exit 2; rm -f psim-f1.done f1-psim-fail.txt
n=$(ls $R/2*/done 2>/dev/null | wc -l); [ "$n" -ge 251 ] || { echo "NOT_READY done=$n" >> f1-psim-fail.txt; exit 3; }
cat $R/2*/fail.txt | grep . >> f1-psim-fail.txt && exit 4
while read d m; do
  [ -e $R/$d/done ] || continue
  mkdir -p s-r/${d:0:7}; [ -e s-r/${d:0:7}/$d ] || cp -rs $R/$d/out/$d s-r/${d:0:7}/
done < /data/tk065/days/days.tsv
one() { mo=$1
  [ -e r-b/$mo/busy-replay.txt ] || python3 $TB/busy-replay.py s-r/$mo r-b/$mo > busy-f1-$mo.log 2>&1 || { echo "FAILBUSY $mo" >> f1-psim-fail.txt; return; }
  d1=$(ls s-r/$mo | head -1); args=()
  while read f s; do args+=(--variant "$s@$f=$s/$f"); done < $R/$d1/cells.txt
  for cap in 0 3; do
    python3 $T/portfolio-sim.py --epoch "$mo=$O/r-b:$mo" "${args[@]}" --drop $DROP --deposit-usd 2500 --position-usd 500 --max-pos $cap \
      --closes-out closes-f1-cap$cap-$mo.json > psim-f1-cap$cap-$mo.txt 2> psim-f1-cap$cap-$mo.log || echo "FAIL f1 $cap $mo" >> f1-psim-fail.txt
  done; }
export -f one; export O R TB T DROP
ls s-r | sort | xargs -P "$P" -I{} bash -c 'one {}'
touch psim-f1.done
