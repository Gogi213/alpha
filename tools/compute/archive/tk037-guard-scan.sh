#!/usr/bin/env bash
# TK-037: защита bounce-grid от смешанных сеток записи — скан всех монето-месяцев vroots.
# Отказ по сетке приходит до расчёта (за секунды); timeout 25 => защита пройдена. Итог: $OUT/summary.txt
set -uo pipefail
NEW=${NEW:-/opt/alpha-compute/bin/alpha-tk037-guard}; V=/data/tk037/vroots; G=/data/tk037/gate-roots/MET; OUT=/data/tk037/guard-scan
RTT="--median-rtt-ns place=4200000,cancel=3980000,taker=5650000 --p95-rtt-ns place=4790000,cancel=4550000,taker=6420000"
rm -rf "$OUT"; mkdir -p "$OUT/roots"
one() { # month sym
  m=$1; s=$2; r=$OUT/roots/$m-$s; mkdir -p "$r"
  for f in "$V/$m/$s"-20??-??-??.binlog; do ln -sf "$(readlink -f "$f")" "$r/"; done
  cp "$G/session.json" "$G/instruments.csv" "$r/"; echo ok > "$r/verify-$s.status"
  timeout 25 nice -n 10 "$NEW" lob bounce-grid --root "$r" --symbol "$s" $RTT --order-usd 1000 --allow-unverified --queue-model prob:3 \
    --threads 1 --h3-mode notional --h3-usd 10000 --stop-form pct1 --take-form 1to1 --out-dir "$r/out" > "$r/log" 2>&1
  rc=$?; echo "$m $s rc=$rc $(grep -m1 'смешанные' "$r/log" | cut -c1-200)" >> "$OUT/summary.txt"; rm -rf "$r/out"
}
export -f one; export NEW V G OUT RTT
for m in $(ls $V); do ls $V/$m | grep -o '^[A-Z0-9]*USDT' | sort -u | sed "s/^/$m /"; done | xargs -P 12 -L1 bash -c 'one $0 $1'
touch "$OUT/DONE"
