#!/usr/bin/env bash
# Проба журнала на одних сутках (calc свободен): день 2026-03-07 (mar) → /data/p12r3a/jrn-probe/<сутки>; сверка с волной (tape-log 30):
# rounds.csv без трёх хвостовых колонок журнала побайтно равен волновому. Вывод — в /data/p12r3a/jrn-probe/cmp.txt
set -uo pipefail
d=${1:-2026-03-07}; mon=${2:-mar}; P=/data/p12r3a/jrn-probe; mkdir -p $P
O=$P bash /data/tk0115/tk115-jrn-day.sh "$d" "$mon" "$d"
w=/data/tk0115/delta/wave/$d/signals/$d/t-bid-btc4h-q1/rounds.csv
{ for k in 60 15; do f=$(ls $P/$d/w$k/$d/t-bid-btc4h-q1/rounds.csv 2>/dev/null || ls $P/$d/w$k/t-bid-btc4h-q1/rounds.csv)
  echo "w$k $f"; echo "cols_new=$(grep -v '^#' $f | head -1 | tr ',' '\n' | wc -l) cols_wave=$(grep -v '^#' $w | head -1 | tr ',' '\n' | wc -l)"
  echo "md5_no_tail_new=$(grep -v '^#' $f | cut -d, -f1-$(( $(grep -v '^#' $f | head -1 | tr ',' '\n' | wc -l) - 3 )) | md5sum | cut -c1-32)"
  echo "md5_no_tail_wave=$(grep -v '^#' $w | cut -d, -f1-$(( $(grep -v '^#' $w | head -1 | tr ',' '\n' | wc -l) - 3 )) | md5sum | cut -c1-32)"; done; cat $P/units.txt; cat $P/$d/fail.txt; } > $P/cmp.txt 2>&1
touch $P/probe.done
