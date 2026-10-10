#!/bin/bash
# TK-114 ярус 1: B1 + стоп 3 %/4 % (дерево TK-084 p12) -> busy-replay -> portfolio-sim --drop v171c, cap 0/3, янв–сен
cd /data/tk0114 || exit 2
T=/data/tk083/tools; DROP=TRUMPUSDT,TRXUSDT,BCHUSDT
printf '%s
' ladder3x0..0.0409sw2-pct2-tr1x1-14400-ttl1800 ladder3x0..0.0409sw2-pct3-tr1x1-14400-ttl1800 ladder3x0..0.0409sw2-pct4-tr1x1-14400-ttl1800 > forms.txt
for m in 2026-01 2026-02 2026-03 2026-04 2026-05 2026-06 2026-07 2026-08 2026-09; do
  python3 prep.py $m || exit 3
  python3 $T/busy-replay.py off/$m on/$m --sets t-bid-btc4h-q1 > on-$m.log 2>&1 || exit 4
  args=(); for f in $(cat forms.txt); do args+=(--variant "$f=t-bid-btc4h-q1/$f"); done
  for cap in 0 3; do
    python3 /data/tk0113/tools/portfolio-sim.py --epoch "$m=/data/tk0114/on:$m" "${args[@]}" --drop $DROP --deposit-usd 2500 --position-usd 500 --max-pos $cap --closes-out closes-cap$cap-$m.json --closes-sym-out closessym-cap$cap-$m.json > psim-cap$cap-$m.txt 2> psim-cap$cap-$m.log || exit 5
  done
  echo "ok $m" >> progress.txt
done
touch tier1.done
