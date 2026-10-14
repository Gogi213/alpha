#!/bin/bash
# TK-241: psim запуска формы (cap 0 и 3, v171c, drop TRUMP/TRX/BCH) по суткам <RUN>/out -> <RUN>/psim/closes-p14f1-cap<N>-<мес>.json.
# Как tools/compute/tk231-p14f1-psim.sh (раскладка out та же: run-<мес>-<сутки>/out/<сутки>), но R/D/O из каталога запуска, число суток — из days.txt.
#   psim-launch.sh <RUN>; env PM (параллельных месяцев, 3). Нейросетей не зовёт (В-6). Ошибка -> <RUN>/psim/p14f1-psim-fail.txt.
set -u
RUN=$1; O=$RUN/psim; R=$RUN/out; D=$RUN/days.txt; TB=/data/tk083/tools; T=/data/tk0113/tools; DROP=TRUMPUSDT,TRXUSDT,BCHUSDT; P=${PM:-3}
mkdir -p $O; cd $O || exit 2; rm -f psim-p14f1.done p14f1-psim-fail.txt
need=$(grep -c . $D); n=$(ls -d $R/run-*/ | while read x; do [ -e $x/done ] && echo 1; done | wc -l); [ "$n" -ge "$need" ] || { echo "NOT_READY done=$n need=$need" >> p14f1-psim-fail.txt; exit 3; }
cat $R/run-*/fail.txt 2>/dev/null | grep . >> p14f1-psim-fail.txt && exit 4
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
[ -e p14f1-psim-fail.txt ] && exit 5
touch psim-p14f1.done
