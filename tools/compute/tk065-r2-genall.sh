#!/bin/bash
# tk065-r2-genall.sh: посуточные скрипты R2 (r2-*, r2t-*) по всем суткам пула; список суток — /data/tk065/days/days.tsv (сутки мес)
out=/data/tk065/days; mkdir -p $out; : > $out/days.tsv
for M in jan feb mar apr may jun jul aug sep oct; do
  for J in /data/tk046/$M/home/alpha/tmp-p07/cells-by-day/jall-$M-20??-??-??.sh; do
    [ -e "$J" ] || continue
    d=${J##*jall-$M-}; d=${d%.sh}
    python3 /data/tk065/r2-gen.py $J $d $out > /dev/null && printf '%s %s\n' $d $M >> $out/days.tsv
  done
done
wc -l < $out/days.tsv
