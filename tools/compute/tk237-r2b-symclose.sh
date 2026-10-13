#!/bin/bash
# TK-237: closes с монетой ([мс, $, монета]) для 31 клетки R2-B (в т.ч. Г-133 chase a=1/2,1,2) — закрывает «нет данных» плато.
# Те же деревья, что tk120-r2b-psim.sh (янв–июль: r-b; авг–сен: r-bf с досчитанными символо-сутками, tk190-psim.sh), cap 0, только --closes-sym-out.
# -> /data/p12r3a/closes-sym-r2b-cap0-<мес>.json, метка sym-r2b.done.
set -u
O=/data/p12r3a; R=$O/r2b; T=/data/tk0113/tools; DROP=TRUMPUSDT,TRXUSDT,BCHUSDT
cd $O || exit 2; rm -f sym-r2b.done sym-r2b-fail.txt
one() { mo=$1; tr=r-b; [ "$mo" = 2026-08 -o "$mo" = 2026-09 ] && tr=r-bf
  [ -e closes-sym-r2b-cap0-$mo.json ] && return
  d1=$(ls s-r/$mo | head -1); args=()
  while read f s; do args+=(--variant "$s@$f=$s/$f"); done < $R/$d1/cells.txt
  python3 $T/portfolio-sim.py --epoch "$mo=$O/$tr:$mo" "${args[@]}" --drop $DROP --deposit-usd 2500 --position-usd 500 --max-pos 0 \
    --closes-sym-out closes-sym-r2b-cap0-$mo.json > psim-sym-r2b-$mo.txt 2> psim-sym-r2b-$mo.log || echo "FAIL sym-r2b $mo" >> sym-r2b-fail.txt; }
export -f one; export O R T DROP
ls s-r | sort | xargs -P 3 -I{} bash -c 'one {}'
[ -e sym-r2b-fail.txt ] || touch sym-r2b.done
