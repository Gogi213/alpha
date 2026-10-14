#!/bin/bash
# TK-231: сделки прохода П-14 F1 по 56 гипотезам (501 хеш × 251 сутки, /data/tk0231/out/run-<мес>-<сутки>/out/<сутки>) -> деревья по месяцу -> busy-replay
# -> portfolio-sim cap 0 и 3 с --drop TRUMP/TRX/BCH (v171c, В-210) -> /data/tk0231/psim/closes-p14f1-cap<N>-<мес>.json (порядок клеток = cells.txt месяца).
# Как tk196-f1-psim.sh (раскладка out другая: метка = сутки). Метка psim-p14f1.done.
set -u
O=/data/tk0231/psim; R=/data/tk0231/out; D=/data/tk0231/days.txt; TB=/data/tk083/tools; T=/data/tk0113/tools; DROP=TRUMPUSDT,TRXUSDT,BCHUSDT; P=${PM:-3}
mkdir -p $O; cd $O || exit 2; rm -f psim-p14f1.done p14f1-psim-fail.txt
n=$(ls -d $R/run-*/ | while read x; do [ -e $x/done ] && echo 1; done | wc -l); [ "$n" -ge 251 ] || { echo "NOT_READY done=$n" >> p14f1-psim-fail.txt; exit 3; }
cat $R/run-*/fail.txt | grep . >> p14f1-psim-fail.txt && exit 4
while read m d; do
  mkdir -p s-r/${d:0:7}; [ -e s-r/${d:0:7}/$d ] || cp -rs $R/run-$m-$d/out/$d s-r/${d:0:7}/
done < $D
one() { mo=$1
  [ -e r-b/$mo/busy-replay.txt ] || python3 $TB/busy-replay.py s-r/$mo r-b/$mo > busy-p14f1-$mo.log 2>&1 || { echo "FAILBUSY $mo" >> p14f1-psim-fail.txt; return; }
  d1=$(ls s-r/$mo | head -1); args=()
  while read f s; do args+=(--variant "$s@$f=$s/$f"); done < $(ls -d $R/run-*-$d1/)cells.txt
  for cap in 0 3; do
    python3 $T/portfolio-sim.py --epoch "$mo=$O/r-b:$mo" "${args[@]}" --drop $DROP --deposit-usd 2500 --position-usd 500 --max-pos $cap \
      --closes-out closes-p14f1-cap$cap-$mo.json > psim-p14f1-cap$cap-$mo.txt 2> psim-p14f1-cap$cap-$mo.log || echo "FAIL p14f1 $cap $mo" >> p14f1-psim-fail.txt
  done; }
export -f one; export O R TB T DROP
ls s-r | sort | xargs -P "$P" -I{} bash -c 'one {}'
touch psim-p14f1.done
