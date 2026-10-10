#!/usr/bin/env bash
# Гейт TK-037 (5246beae): bounce-grid сутки без v4 — байт в байт против прежнего бинарника; v4-сутки — прогон без отказа.
#   tk037-qty-gate.sh <root> <SYM> <YYYY-MM-DD>   env: OLD NEW OUT THREADS
set -uo pipefail
R=${1:?root}; S=${2:?SYM}; D=${3:?day}
OLD=${OLD:-/opt/alpha-compute/bin/alpha-tk037-validate}; NEW=${NEW:-/opt/alpha-compute/bin/alpha-tk037-qty}
OUT=${OUT:-/data/tk037/qty-gate}; mkdir -p "$OUT"
RTT="--median-rtt-ns place=4200000,cancel=3980000,taker=5650000 --p95-rtt-ns place=4790000,cancel=4550000,taker=6420000"
for v in old new; do
  b=$OLD; [ $v = new ] && b=$NEW
  rm -rf "$OUT/$S-$D-$v"
  nice -n 10 "$b" lob bounce-grid --root "$R" --symbol "$S" --day "$D" $RTT --order-qty-from-pool --queue-model prob:3 \
    --threads "${THREADS:-2}" --h3-mode notional --h3-usd 10000 --min-flow-pct 100 --stop-form pct1 --take-form 1to1 \
    --out-dir "$OUT/$S-$D-$v" > "$OUT/$S-$D-$v.log" 2>&1; echo "$v rc=$?"
done
for f in $(cd "$OUT/$S-$D-old" && find . -type f | sort); do cmp -s "$OUT/$S-$D-old/$f" "$OUT/$S-$D-new/$f" && echo "OK $f" || echo "DIFF $f"; done
