#!/bin/bash
# TK-237: closes B1 с монетой ([мс, $, монета]) для парной ΔSR сита — деревья b1-b TK-191 (та же вселенная R2-B, v171c), cap 0, только --closes-sym-out.
# -> /data/p12r3a/closes-sym-b1-cap0-<мес>.json, метка sym-b1.done.
set -u
O=/data/p12r3a; T=/data/tk0113/tools; DROP=TRUMPUSDT,TRXUSDT,BCHUSDT
F=ladder3x0..0.0409sw2-pct2-tr1x1-14400-ttl1800; S=t-bid-btc4h-q1; V="$S@$F=$S/$F"
cd $O || exit 2; rm -f sym-b1.done sym-b1-fail.txt
for mo in $(ls b1-b | sort); do
  python3 $T/portfolio-sim.py --epoch "$mo=$O/b1-b:$mo" --variant "$V" --drop $DROP --deposit-usd 2500 --position-usd 500 --max-pos 0 \
    --closes-sym-out closes-sym-b1-cap0-$mo.json > psim-sym-b1-$mo.txt 2> psim-sym-b1-$mo.log || echo "FAIL $mo" >> sym-b1-fail.txt &
  while [ "$(jobs -r | wc -l)" -ge 3 ]; do sleep 1; done
done
wait
[ -e sym-b1-fail.txt ] || touch sym-b1.done
