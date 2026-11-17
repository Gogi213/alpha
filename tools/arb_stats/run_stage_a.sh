#!/bin/bash
# Этап A на srv: последний запуск каждой биржи за сутки <день>, до приёма <end_ms>. Использование: run_stage_a.sh <день> <end_ms> <out>
set -u
DAY=$1; END=$2; OUT=$3; HERE=$(cd "$(dirname "$0")" && pwd)
declare -A ROOT=( [binance]=/data/collect/binance/data [bybit]=/data/turbo-probe/soak-bybit/data [okx]=/data/collect/okx/data [bingx]=/data/collect/bingx/data )
MINRUN=${MINRUN:-1794656000}     # начало последнего запуска (11:46 UTC 14.11, сборка final6j)
jobs_file=$(mktemp)
for ex in binance bybit okx bingx; do
  for f in ${ROOT[$ex]}/$DAY/book*.tsv.zst; do
    run=$(basename "$f" | sed -E 's/^book[0-9]+-([0-9]+)\.tsv\.zst$/\1/')
    [ "$run" -ge "$MINRUN" ] || continue
    tag=$(basename "$f" | sed -E 's/-.*//')
    echo "$ex $tag $f" >> "$jobs_file"
  done
done
wc -l < "$jobs_file"
xargs -a "$jobs_file" -P "${PAR:-4}" -L 1 bash -c 'python3 '"$HERE"'/parse_book.py $0 '"$DAY"' '"$END"' '"$OUT"' $1 $2'
echo done
