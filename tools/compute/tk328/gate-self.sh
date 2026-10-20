#!/bin/bash
# TK-328, П-22 §6, гейт «байт в байт»: self-проекция. Спот == фьючерс (один и тот же набор бинлога) ⇒ `lob touches-project`
# даёт CSV, равный входному, а `bounce-grid --touches-from` по нему — те же rounds.csv/forms.csv, что по исходному.
# Запуск на calc под alsched (--kind gate --cores 3): gate-self.sh <бинарник> <OUT> <корень эпохи> <SYM> <YYYY-MM-DD>
set -euo pipefail
BIN=$1 OUT=$2 SRC=$3 SYM=$4 DAY=$5
R=$OUT/root
rm -rf "$OUT"; mkdir -p "$R" "$OUT/orig/$DAY" "$OUT/proj/$DAY"
for f in "$SRC/$SYM-$DAY.binlog" "$SRC/$SYM-$DAY.binlog.zst" "$SRC/verify-$SYM.status" "$SRC/instruments.csv" "$SRC/session.json"; do
  [ -e "$f" ] && ln -s "$f" "$R/"
done
PARTS=("$R/$SYM-$DAY.binlog"*)
H3=(--h3-mode notional --h3-usd 10000)
"$BIN" lob touches --root "$R" --symbol "$SYM" "${H3[@]}" --out "$OUT/orig/$DAY/touches-$SYM.csv"
"$BIN" lob touches-project --in "$OUT/orig/$DAY/touches-$SYM.csv" --out "$OUT/proj/$DAY/touches-$SYM.csv" \
  --spot "${PARTS[@]}" --fut "${PARTS[@]}" | tee "$OUT/project-report.txt"
cmp "$OUT/orig/$DAY/touches-$SYM.csv" "$OUT/proj/$DAY/touches-$SYM.csv" && echo "GATE1 touches-csv: байт в байт"
for V in orig proj; do
  "$BIN" lob bounce-grid --root "$R" --symbol "$SYM" --day "$DAY" --touches-from "$OUT/$V" "${H3[@]}" \
    --queue-model prob:3 \
    --median-rtt-ns place=4200000,cancel=3980000,taker=5650000 --p95-rtt-ns place=4790000,cancel=4550000,taker=6420000 \
    --order-usd 100 --stop-form behind --take-form 1to1 --deadline-secs 600 --threads 3 --out-dir "$OUT/grid-$V"
done
for f in rounds.csv forms.csv; do cmp "$OUT/grid-orig/$f" "$OUT/grid-proj/$f" && echo "GATE2 $f: байт в байт ($(wc -l < "$OUT/grid-orig/$f") строк)"; done
echo "touches строк: $(wc -l < "$OUT/orig/$DAY/touches-$SYM.csv")"
