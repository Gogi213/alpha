#!/usr/bin/env bash
# TK-609 G-A3 (R4, шаг 1): ftab.csv, собранный кодом TK-599 из таблиц Rust, байт в байт равен ftab.csv, собранному из таблиц Python.
# Вход ftab-сборки идентичен (те же eps-*.parquet, тот же sigsx); единственное отличие — `tk594-<режим>-<сторона>.parquet`: берётся выход Rust из G-A1.
# Сторона/окно/поток — как у шести таблиц G-A1. Запуск на srv (nice 19).
set -uo pipefail
CODE=${CODE:-/data/p42-tk609/code-sw}; G1=${G1:-/data/p42-tk609/ga1}; OUT=${OUT:-/data/p42-tk609/ga3}; BIN=${BIN:-/data/p42-tk609/bin/alpha-feat}
declare -A BD=([wa]=2026-06-17 [wb]=2026-07-25 [wc]=2026-09-01); rc=0
mkdir -p "$OUT"
for w in wa wb wc; do
  for spec in "f686957305744 L deriv long" "fa95205e801e5 S flow short"; do
    set -- $spec; vid=$1; side=$2; mode=$3; sname=$4
    src=/data/p42-tk599/ftab/$w/$vid; d=$OUT/$w/$vid; mkdir -p "$d"
    cp "$src/eps-$side.parquet" "$d/"; cp "$G1/$w-$side.parquet" "$d/tk594-$mode-$side.parquet"
    TK594_METRICS=/data/p42-tk599/metrics-all TK594_TRADES=/data/p42-tk599/$w/aggtrades P42_FEAT_BIN=$BIN nice -n "${NICE:-19}" python3 "$CODE/tools/p42/retitr_ftab.py" build "$side" \
      --sigsx /data/p42-tk599/sx/$w/$vid/$sname-sigsx.csv --work "$d" --out "$d/ftab.csv" --prod /data/tk594/py --cache /data/p42-tk599/$w/cache \
      --trades /data/p42-tk599/$w/aggtrades --procs 4 --base-date "${BD[$w]}" > "$d/build.log" 2>&1 || { echo "$w $side: build rc!=0"; rc=1; continue; }
    if cmp -s "$d/ftab.csv" "$src/ftab.csv"; then r=IDENTICAL; else r=DIFFERENT; rc=1; fi
    echo "$w $side $vid ftab.csv $r md5=$(md5sum < "$d/ftab.csv" | cut -c1-12) rows=$(($(wc -l < "$d/ftab.csv")-1)) py_md5=$(md5sum < "$src/ftab.csv" | cut -c1-12)"
  done
done
echo "GA3 rc=$rc"; echo $rc > "$OUT/rc"
