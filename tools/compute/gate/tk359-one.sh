#!/usr/bin/env bash
# TK-359: одна единица боевого прогона — `lob seconds` на монето-сутки → <OUT>/<cls>/<сутки>/<SYM>-<сутки>.csv.gz (атомарно).
#   tk359-one.sh <корень суток> <SYM> <сутки> <fut|spot>      env: BIN OUT
# Готовая единица (непустой .gz) пропускается. Отказ K1 (нет verify == ok) и любой rc≠0 — строка в $OUT/skip.log, rc 0
# (проход не рвётся; число пропусков — в итоге прогона). Пустой/частичный выход не остаётся.
set -uo pipefail
root=$1 sym=$2 day=$3 cls=$4
BIN=${BIN:-/data/tk0359/new/alpha-new}; OUT=${OUT:-/data/tk0359/series}
dst=$OUT/$cls/$day; gz=$dst/$sym-$day.csv.gz
[ -s "$gz" ] && exit 0
mkdir -p "$dst"; tmp=$(mktemp -d "$OUT/.tmp-XXXXXX")
if nice -n 5 "$BIN" lob seconds --root "$root" --symbol "$sym" --day "$day" --out "$tmp" > "$tmp/log" 2>&1 \
   && [ -s "$tmp/$sym-$day.csv" ] && gzip -1 -c "$tmp/$sym-$day.csv" > "$gz.tmp" && mv "$gz.tmp" "$gz"; then
  rm -rf "$tmp"
else
  echo "$cls $day $sym $(tail -n1 "$tmp/log" | tr '\t\n' '  ' | cut -c1-200)" >> "$OUT/skip.log"
  rm -rf "$tmp" "$gz.tmp"
fi
exit 0
