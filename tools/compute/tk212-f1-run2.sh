#!/usr/bin/env bash
# TK-212: остаток F1 через tk212-f1-day2.sh (две пачки std/tape); сутки без claim/done, тяжёлые (больше символов B1) первыми. Метка f1b.done.
#   tk212-f1-run2.sh   env: D=4 GT=3
set -uo pipefail
D=${D:-4}; export GT=${GT:-3}; T=/data/tk0196; OUT=$T/out
awk -F, 'NR>1{n[$1" "$2]++} END{for(k in n) print n[k], k}' /data/tk0115/delta/b1-symdays.csv | sort -k1,1nr -k3,3 \
  | while read -r c m d; do [ -e "$OUT/$d/claim" ] || [ -e "$OUT/$d/done" ] || echo "$m $d"; done > /data/tk0212/f1b-list.txt
wc -l < /data/tk0212/f1b-list.txt
xargs -P "$D" -L1 bash -c 'bash /data/tk0212/tk212-f1-day2.sh "$1" "$0" "$1"' < /data/tk0212/f1b-list.txt 2>&1
touch /data/tk0212/f1b.done
