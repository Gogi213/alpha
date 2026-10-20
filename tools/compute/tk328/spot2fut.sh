#!/bin/bash
# TK-328, П-22 §6, [вживую]: касания по бинлогу СПОТА → проекция в цену фьюча → bounce-grid на бинлоге ФЬЮЧА.
# Запуск на calc под alsched: spot2fut.sh <бинарник> <OUT> <корень спота> <корень фьюча> <SYM> <YYYY-MM-DD>
set -euo pipefail
BIN=$1 OUT=$2 SS=$3 FS=$4 SYM=$5 DAY=$6
rm -rf "$OUT"; mkdir -p "$OUT/spot-root" "$OUT/fut-root" "$OUT/spot/$DAY" "$OUT/proj/$DAY"
for pair in "$SS:spot-root" "$FS:fut-root"; do
  src=${pair%%:*} dst=$OUT/${pair##*:}
  for f in "$src/$SYM-$DAY.binlog" "$src/$SYM-$DAY.binlog.zst" "$src/verify-$SYM.status" "$src/instruments.csv" "$src/session.json"; do
    [ -e "$f" ] && ln -s "$f" "$dst/"
  done
done
SP=("$OUT/spot-root/$SYM-$DAY.binlog"*) FP=("$OUT/fut-root/$SYM-$DAY.binlog"*)
H3=(--h3-mode notional --h3-usd 10000)
"$BIN" lob touches --root "$OUT/spot-root" --symbol "$SYM" "${H3[@]}" --out "$OUT/spot/$DAY/touches-$SYM.csv"
"$BIN" lob touches-project --in "$OUT/spot/$DAY/touches-$SYM.csv" --out "$OUT/proj/$DAY/touches-$SYM.csv" \
  --spot "${SP[@]}" --fut "${FP[@]}" | tee "$OUT/project-report.txt"
"$BIN" lob bounce-grid --root "$OUT/fut-root" --symbol "$SYM" --day "$DAY" --touches-from "$OUT/proj" "${H3[@]}" \
  --queue-model prob:3 \
  --median-rtt-ns place=4200000,cancel=3980000,taker=5650000 --p95-rtt-ns place=4790000,cancel=4550000,taker=6420000 \
  --order-usd 100 --stop-form behind --take-form 1to1 --deadline-secs 600 --threads 3 --out-dir "$OUT/grid"
echo "touches спот: $(($(wc -l < "$OUT/spot/$DAY/touches-$SYM.csv")-1)) · проекция: $(($(wc -l < "$OUT/proj/$DAY/touches-$SYM.csv")-1)) · кругов: $(($(grep -vc '^#' "$OUT/grid/rounds.csv")-1))"
