#!/bin/bash
# TK-327: замер пика памяти на 10 сутках (спот+фьюч) -> стоп, если RSS > 1,5 ГБ -> полный спот -> полный фьюч.
# Max RSS каждого шага пишется в mem-full.txt (/usr/bin/time -v). Выходы — /data/tk0327.
cd /data/tk0327 || exit 2
export TK327_TMP=/data/tk0327
rss() { grep -h "Maximum resident" "$1" | awk '{print $NF}'; }
for m in spot fut; do
  /usr/bin/time -v python3 features.py $m mem10-$m.csv --workers 2 --limit-days 10 > mem10-$m.log 2> mem10-$m.time || exit 3
  echo "$m 10d maxrss_kb $(rss mem10-$m.time)" >> mem-full.txt
  [ "$(rss mem10-$m.time)" -lt 1500000 ] || { echo "RSS 10 суток > 1,5 ГБ ($m) — стоп" >> mem-full.txt; exit 4; }
done
for m in spot fut; do
  /usr/bin/time -v python3 features.py $m $m-features.csv --workers 2 > $m-full.log 2> $m-full.time || exit 5
  echo "$m full maxrss_kb $(rss $m-full.time)" >> mem-full.txt
done
echo done > DONE
