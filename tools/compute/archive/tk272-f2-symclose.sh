#!/bin/bash
# TK-272: closes с монетой ([мс, $, монета]) для сита F2 по половине А — те же деревья и psim cap 0, что psim-launch.sh, только --closes-sym-out.
#   tk272-f2-symclose.sh <RUN>  (RUN = каталог запуска формы с out/, psim/s-r, psim/r-b, days.txt); env PM (месяцев параллельно, 3)
# -> <RUN>/psim/closes-sym-p14f1-cap0-<мес>.json, метка sym.done; ошибка -> sym-fail.txt.
set -u
RUN=$1; O=$RUN/psim; R=$RUN/out; T=/data/tk0113/tools; DROP=TRUMPUSDT,TRXUSDT,BCHUSDT; P=${PM:-3}
cd $O || exit 2; rm -f sym.done sym-fail.txt
one() { mo=$1
  [ -e closes-sym-p14f1-cap0-$mo.json ] && return
  d1=$(ls s-r/$mo | head -1); args=()
  while read f s; do args+=(--variant "$s@$f=$s/$f"); done < $(ls -d $R/run-*-$d1/)cells.txt
  python3 $T/portfolio-sim.py --epoch "$mo=$O/r-b:$mo" "${args[@]}" --drop $DROP --deposit-usd 2500 --position-usd 500 --max-pos 0 \
    --closes-sym-out closes-sym-p14f1-cap0-$mo.json > psim-sym-$mo.txt 2> psim-sym-$mo.log || echo "FAIL sym $mo" >> sym-fail.txt; }
export -f one; export O R T DROP
ls s-r | sort | xargs -P "$P" -I{} bash -c 'one {}'
[ -e sym-fail.txt ] || touch sym.done
