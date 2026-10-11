#!/bin/bash
# TK-048 пара раскладки по занятости: 2 из каждых MOD оставшихся на sdb файлов D20 (>MINB, по убыванию размера) -> копия на sda5, симлинк переставляется.
# usage: apprsplit2.sh <E=D20 месяца> <A=каталог копий на sda> <день1> <день2> <YYYY-MM> [MOD=3] [MINB=200000]
#        apprsplit2.sh --undo <лог>   (возвращает симлинки на прежние цели; копии остаются)
set -u
if [ "${1:-}" = "--undo" ]; then
  while read -r link old _; do ln -sfn "$old" "$link.new" && mv -T "$link.new" "$link"; done < "$2"; exit 0
fi
E=$1; A=$2; D1=$3; D2=$4; PFX=$5; MOD=${6:-3}; MINB=${7:-200000}
LOG=${APPR_LOG:-/data/tk048/apprsplit2-$PFX.log}; : > "$LOG"
exec 7>/data/tk-bench.lock; flock -s 7
for n in $(seq -w "$D1" "$D2"); do d=$PFX-$n; mkdir -p "$A/$d"
  ls -L -l "$E/$d" | awk -v m="$MINB" '$5>m && $NF ~ /\.csv$/ {print $5, $NF}' | sort -rn | while read -r _ nm; do
    cur=$(readlink "$E/$d/$nm"); case "$cur" in /alpha-sda/*) continue;; esac; echo "$nm $cur"
  done | awk -v m="$MOD" 'NR%m!=0' | while read -r nm cur; do
    src=$(readlink -f "$E/$d/$nm")
    ionice -c3 cp --reflink=never "$src" "$A/$d/$nm.tmp" && mv "$A/$d/$nm.tmp" "$A/$d/$nm" && cmp -s "$src" "$A/$d/$nm" \
      && ln -sfn "$A/$d/$nm" "$E/$d/$nm.new" && mv -T "$E/$d/$nm.new" "$E/$d/$nm" \
      && echo "$E/$d/$nm $cur $(stat -c %s "$src")" >> "$LOG" || echo "FAIL $d $nm" >> "$LOG"
  done
done
touch "${LOG%.log}.done"
