#!/bin/bash
# TK-083: B2 (потолок 3 позиции, П-12 §6 п.1) — portfolio-sim по готовым on/<мес> (после busy-replay)
cd /data/tk083 || exit 2
T=/data/tk083/tools
for m in 2026-01 2026-02 2026-03 2026-04 2026-05 2026-06 2026-07 2026-08 2026-09 2026-10; do
  args=(); for f in $(cat forms.txt); do args+=(--variant "$f=t-bid-btc4h-q1/$f"); done
  python3 $T/portfolio-sim.py --epoch "$m=/data/tk083/on:$m" "${args[@]}" --deposit-usd 2500 --position-usd 500 --max-pos 3 --json psimB2-$m.json --closes-out closesB2-$m.json > psimB2-$m.txt 2> psimB2-$m.log || exit 5
done
touch b2.done
