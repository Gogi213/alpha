#!/usr/bin/env bash
# TK-212: остаток F1 через tk212-f1-day2.sh (две пачки std/tape); сутки без done, тяжёлые (больше символов B1) первыми. Метка $TAG.done.
#   tk212-f1-run2.sh   env: D=4 GT=3 TAG=f1b. Повторный запуск (продолжение): ждёт конца живых day2/day.sh, снимает осиротевшие claim (без done) и считает недосчитанное; готовые не трогает.
set -uo pipefail
D=${D:-4}; export GT=${GT:-3}; TAG=${TAG:-f1b}; T=/data/tk0196; OUT=$T/out; L=/data/tk0212/$TAG-list.txt
while pgrep -f 'tk212-f1-day2.sh|tk0196/day.sh' > /dev/null; do sleep 30; done
for c in "$OUT"/*/claim; do [ -d "$c" ] || continue; dd=$(dirname "$c"); [ -e "$dd/done" ] || rmdir "$c" 2>/dev/null; done
awk -F, 'NR>1{n[$1" "$2]++} END{for(k in n) print n[k], k}' /data/tk0115/delta/b1-symdays.csv | sort -k1,1nr -k3,3 \
  | while read -r c m d; do [ -e "$OUT/$d/done" ] || echo "$m $d"; done > "$L"
wc -l < "$L"
xargs -P "$D" -L1 bash -c 'bash /data/tk0212/tk212-f1-day2.sh "$1" "$0" "$1"' < "$L" 2>&1
touch /data/tk0212/$TAG.done
