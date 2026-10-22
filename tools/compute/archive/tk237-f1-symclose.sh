#!/bin/bash
# TK-237: closes F1 П-14 с монетой ([мс, $, монета]) для сита по половине А. Те же деревья r-b и ключи, что tk196-f1-psim.sh, cap 0, только --closes-sym-out.
# -> /data/tk0196/psim/closes-sym-f1-cap0-<мес>.json, метка sym-f1.done.
set -u
O=/data/tk0196/psim; R=/data/tk0196/out; T=/data/tk0113/tools; DROP=TRUMPUSDT,TRXUSDT,BCHUSDT; P=${PM:-3}
cd $O || exit 2; rm -f sym-f1.done sym-f1-fail.txt
one() { mo=$1
  [ -e closes-sym-f1-cap0-$mo.json ] && return
  d1=$(ls s-r/$mo | head -1); args=()
  while read f s; do args+=(--variant "$s@$f=$s/$f"); done < $R/$d1/cells.txt
  python3 $T/portfolio-sim.py --epoch "$mo=$O/r-b:$mo" "${args[@]}" --drop $DROP --deposit-usd 2500 --position-usd 500 --max-pos 0 \
    --closes-sym-out closes-sym-f1-cap0-$mo.json > psim-sym-f1-$mo.txt 2> psim-sym-f1-$mo.log || echo "FAIL sym $mo" >> sym-f1-fail.txt; }
export -f one; export O R T DROP
ls s-r | sort | xargs -P "$P" -I{} bash -c 'one {}'
[ -e sym-f1-fail.txt ] || touch sym-f1.done
