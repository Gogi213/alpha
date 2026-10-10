#!/bin/bash
# TK-114 П-13 §6 п.1: ОДИН тест победителя яруса 2 (g93-u1-K3 = -pynw3u3) и B1 на половине Б: portfolio-sim --drop = v171c + половина А, cap 0/3, янв–сен.
# Выход /data/tk0114/B/closes[sym]-cap<0|3>-<мес>.json; метка /data/tk0114/B/done.
set -u
O=/data/tk0114/B; T=/data/tk0113/tools; D=/data/tk065; DROP=$(cat /data/tk0114/dropA.txt); mkdir -p $O; cd $O || exit 2; rm -f done fail.txt
args=(); while read f s; do case $f in *-pynw3u3|ladder3x0..0.0409sw2-pct2-tr1x1-14400-ttl1800) [ $s = t-bid-btc4h-q1 ] && args+=(--variant "$s@$f=$s/$f");; esac; done < $D/days/r2-2026-03-10.cells
echo "${#args[@]}" > nvariants.txt
one() { mo=$1; cap=$2
  python3 $T/portfolio-sim.py --epoch "$mo=/data/p12r2/vn-b:$mo" "${args[@]}" --drop "$DROP" --deposit-usd 2500 --position-usd 500 --max-pos $cap \
    --closes-out $O/closes-cap$cap-$mo.json --closes-sym-out $O/closessym-cap$cap-$mo.json > $O/psim-cap$cap-$mo.txt 2> $O/psim-cap$cap-$mo.log || echo "FAIL $cap $mo" >> $O/fail.txt; }
for mo in 2026-01 2026-02 2026-03 2026-04 2026-05 2026-06 2026-07 2026-08 2026-09; do for cap in 0 3; do one $mo $cap & done; wait; done
[ -f fail.txt ] || touch done
